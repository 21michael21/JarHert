from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable


_MODES = frozenset({"coding", "research"})
_STATUSES = frozenset({"queued", "running", "succeeded", "failed", "cancelled"})


@dataclass(frozen=True)
class NativeCodingJob:
    id: int
    tg_user_id: int
    mode: str
    prompt: str
    repository_url: str | None
    source_urls: tuple[str, ...]
    status: str
    idempotency_key: str
    worker_id: str | None
    lease_until: str | None
    heartbeat_at: str | None
    result_text: str | None
    last_error: str | None
    delivery_status: str
    delivery_attempts: int
    created_at: str
    updated_at: str


class NativeCodingJobStore:
    """Durable profile-local queue; a Mac runner claims it over SSH."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path).expanduser()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def enqueue(
        self,
        *,
        tg_user_id: int,
        mode: str,
        prompt: str,
        idempotency_key: str,
        repository_url: str | None = None,
        source_urls: list[str] | tuple[str, ...] | None = None,
    ) -> NativeCodingJob:
        clean_key = _required(idempotency_key, "Idempotency key", 220)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM native_coding_jobs WHERE tg_user_id = ? AND idempotency_key = ?",
                (int(tg_user_id), clean_key),
            ).fetchone()
            if existing is not None:
                return _from_row(existing)
            job_id = int(
                connection.execute(
                    """
                    INSERT INTO native_coding_jobs(
                        tg_user_id, mode, prompt, repository_url, source_urls_json, status, idempotency_key
                    ) VALUES (?, ?, ?, ?, ?, 'queued', ?)
                    """,
                    (
                        _positive(tg_user_id, "Telegram user id"),
                        _allowed(mode, _MODES, "Mode"),
                        _required(prompt, "Prompt", 5000),
                        _optional(repository_url, 500),
                        _json_urls(source_urls or []),
                        clean_key,
                    ),
                ).lastrowid
            )
            row = connection.execute("SELECT * FROM native_coding_jobs WHERE id = ?", (job_id,)).fetchone()
        return _from_row(row)

    def claim_next(
        self,
        *,
        worker_id: str,
        now: datetime | None = None,
        lease_seconds: int = 900,
    ) -> NativeCodingJob | None:
        current = _utc_now(now)
        worker = _required(worker_id, "Worker id", 100)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE native_coding_jobs
                SET status = 'queued', worker_id = NULL, lease_until = NULL, heartbeat_at = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE status = 'running' AND lease_until <= ?
                """,
                (current,),
            )
            row = connection.execute(
                "SELECT * FROM native_coding_jobs WHERE status = 'queued' ORDER BY id LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            lease_until = (datetime.fromisoformat(current) + timedelta(seconds=max(1, min(int(lease_seconds), 3600)))).isoformat()
            result = connection.execute(
                """
                UPDATE native_coding_jobs
                SET status = 'running', worker_id = ?, heartbeat_at = ?, lease_until = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status = 'queued'
                """,
                (worker, current, lease_until, int(row["id"])),
            )
            if result.rowcount != 1:
                return None
            claimed = connection.execute("SELECT * FROM native_coding_jobs WHERE id = ?", (int(row["id"]),)).fetchone()
        return _from_row(claimed)

    def heartbeat(self, job_id: int, *, worker_id: str, lease_seconds: int = 900) -> bool:
        current = _utc_now(None)
        lease_until = (datetime.fromisoformat(current) + timedelta(seconds=max(1, min(int(lease_seconds), 3600)))).isoformat()
        with self._connect() as connection:
            result = connection.execute(
                """
                UPDATE native_coding_jobs SET heartbeat_at = ?, lease_until = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status = 'running' AND worker_id = ?
                """,
                (current, lease_until, int(job_id), _required(worker_id, "Worker id", 100)),
            )
        return result.rowcount == 1

    def complete(self, job_id: int, *, worker_id: str, result_text: str) -> NativeCodingJob:
        return self._finish(job_id, worker_id=worker_id, status="succeeded", result_text=result_text)

    def fail(self, job_id: int, *, worker_id: str, error: str) -> NativeCodingJob:
        return self._finish(job_id, worker_id=worker_id, status="failed", error=error)

    def list_for_user(self, tg_user_id: int, *, limit: int = 20) -> list[NativeCodingJob]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM native_coding_jobs WHERE tg_user_id = ?
                ORDER BY id DESC LIMIT ?
                """,
                (_positive(tg_user_id, "Telegram user id"), max(1, min(int(limit), 100))),
            ).fetchall()
        return [_from_row(row) for row in rows]

    def get_for_user(self, job_id: int, *, tg_user_id: int) -> NativeCodingJob:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM native_coding_jobs WHERE id = ? AND tg_user_id = ?",
                (int(job_id), _positive(tg_user_id, "Telegram user id")),
            ).fetchone()
        if row is None:
            raise LookupError("Coding job not found.")
        return _from_row(row)

    def claim_completed_for_delivery(
        self,
        *,
        worker_id: str,
        now: datetime | None = None,
        lease_seconds: int = 120,
    ) -> NativeCodingJob | None:
        current = _utc_now(now)
        worker = _required(worker_id, "Worker id", 100)
        lease_until = (datetime.fromisoformat(current) + timedelta(seconds=max(1, min(int(lease_seconds), 3600)))).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE native_coding_jobs SET delivery_status = 'pending', delivery_lease_until = NULL
                WHERE delivery_status = 'delivering' AND delivery_lease_until <= ?
                """,
                (current,),
            )
            row = connection.execute(
                """
                SELECT * FROM native_coding_jobs
                WHERE status IN ('succeeded', 'failed') AND delivery_status = 'pending'
                ORDER BY id LIMIT 1
                """
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                """
                UPDATE native_coding_jobs
                SET delivery_status = 'delivering', delivery_worker_id = ?, delivery_lease_until = ?,
                    delivery_attempts = delivery_attempts + 1, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND delivery_status = 'pending'
                """,
                (worker, lease_until, int(row["id"])),
            )
            claimed = connection.execute("SELECT * FROM native_coding_jobs WHERE id = ?", (int(row["id"]),)).fetchone()
        return _from_row(claimed)

    def mark_delivery_sent(self, job_id: int, *, worker_id: str) -> NativeCodingJob:
        return self._finish_delivery(job_id, worker_id=worker_id, delivered=True)

    def release_delivery(self, job_id: int, *, worker_id: str) -> NativeCodingJob:
        return self._finish_delivery(job_id, worker_id=worker_id, delivered=False)

    def _finish(
        self,
        job_id: int,
        *,
        worker_id: str,
        status: str,
        result_text: str | None = None,
        error: str | None = None,
    ) -> NativeCodingJob:
        with self._connect() as connection:
            result = connection.execute(
                """
                UPDATE native_coding_jobs
                SET status = ?, result_text = ?, last_error = ?, lease_until = NULL, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status = 'running' AND worker_id = ?
                """,
                (
                    _allowed(status, _STATUSES, "Status"),
                    _optional(result_text, 20_000),
                    _optional(error, 500),
                    int(job_id),
                    _required(worker_id, "Worker id", 100),
                ),
            )
            if result.rowcount != 1:
                raise PermissionError("Coding job lease lost or belongs to another worker.")
            row = connection.execute("SELECT * FROM native_coding_jobs WHERE id = ?", (int(job_id),)).fetchone()
        return _from_row(row)

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS native_coding_jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tg_user_id INTEGER NOT NULL,
                    mode TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    repository_url TEXT,
                    source_urls_json TEXT NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL DEFAULT 'queued',
                    idempotency_key TEXT NOT NULL,
                    worker_id TEXT,
                    lease_until TEXT,
                    heartbeat_at TEXT,
                    result_text TEXT,
                    last_error TEXT,
                    delivery_status TEXT NOT NULL DEFAULT 'pending',
                    delivery_attempts INTEGER NOT NULL DEFAULT 0,
                    delivery_worker_id TEXT,
                    delivery_lease_until TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(tg_user_id, idempotency_key)
                );
                CREATE INDEX IF NOT EXISTS ix_native_coding_jobs_status_id
                    ON native_coding_jobs(status, id);
                CREATE INDEX IF NOT EXISTS ix_native_coding_jobs_user_id
                    ON native_coding_jobs(tg_user_id, id DESC);
                """
            )
            _add_column(connection, "delivery_status", "TEXT NOT NULL DEFAULT 'pending'")
            _add_column(connection, "delivery_attempts", "INTEGER NOT NULL DEFAULT 0")
            _add_column(connection, "delivery_worker_id", "TEXT")
            _add_column(connection, "delivery_lease_until", "TEXT")

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 10000")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def _finish_delivery(self, job_id: int, *, worker_id: str, delivered: bool) -> NativeCodingJob:
        with self._connect() as connection:
            result = connection.execute(
                """
                UPDATE native_coding_jobs
                SET delivery_status = ?, delivery_worker_id = NULL, delivery_lease_until = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND delivery_status = 'delivering' AND delivery_worker_id = ?
                """,
                ("delivered" if delivered else "pending", int(job_id), _required(worker_id, "Worker id", 100)),
            )
            if result.rowcount != 1:
                raise PermissionError("Coding delivery lease lost or belongs to another worker.")
            row = connection.execute("SELECT * FROM native_coding_jobs WHERE id = ?", (int(job_id),)).fetchone()
        return _from_row(row)


def _from_row(row: sqlite3.Row) -> NativeCodingJob:
    return NativeCodingJob(
        id=int(row["id"]),
        tg_user_id=int(row["tg_user_id"]),
        mode=str(row["mode"]),
        prompt=str(row["prompt"]),
        repository_url=str(row["repository_url"]) if row["repository_url"] else None,
        source_urls=tuple(json.loads(row["source_urls_json"])),
        status=str(row["status"]),
        idempotency_key=str(row["idempotency_key"]),
        worker_id=str(row["worker_id"]) if row["worker_id"] else None,
        lease_until=str(row["lease_until"]) if row["lease_until"] else None,
        heartbeat_at=str(row["heartbeat_at"]) if row["heartbeat_at"] else None,
        result_text=str(row["result_text"]) if row["result_text"] else None,
        last_error=str(row["last_error"]) if row["last_error"] else None,
        delivery_status=str(row["delivery_status"]),
        delivery_attempts=int(row["delivery_attempts"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def dispatch_completed_coding_jobs(
    store: NativeCodingJobStore,
    sender: Callable[[int, str], str | None],
    *,
    worker_id: str = "coding-result-dispatcher",
    limit: int = 20,
) -> dict[str, int]:
    counts = {"claimed": 0, "sent": 0, "failed": 0}
    for _ in range(max(1, min(int(limit), 100))):
        job = store.claim_completed_for_delivery(worker_id=worker_id)
        if job is None:
            break
        counts["claimed"] += 1
        text = job.result_text if job.status == "succeeded" else f"Задача #{job.id} не выполнилась. Попробуй ещё раз."
        try:
            sender(job.tg_user_id, text or f"Задача #{job.id} завершилась без текста.")
        except Exception:
            store.release_delivery(job.id, worker_id=worker_id)
            counts["failed"] += 1
            continue
        store.mark_delivery_sent(job.id, worker_id=worker_id)
        counts["sent"] += 1
    return counts


def _utc_now(value: datetime | None) -> str:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("Current time must have timezone.")
    return current.astimezone(timezone.utc).isoformat()


def _json_urls(values: list[str] | tuple[str, ...]) -> str:
    if len(values) > 10:
        raise ValueError("Source URLs exceed limit 10.")
    return json.dumps([_required(value, "Source URL", 500) for value in values], ensure_ascii=False)


def _allowed(value: str, allowed: frozenset[str], label: str) -> str:
    clean = _required(value, label, 40).casefold()
    if clean not in allowed:
        raise ValueError(f"{label} is not allowlisted.")
    return clean


def _positive(value: int, label: str) -> int:
    clean = int(value)
    if clean <= 0:
        raise ValueError(f"{label} must be positive.")
    return clean


def _required(value: str, label: str, limit: int) -> str:
    clean = str(value or "").strip()
    if not clean:
        raise ValueError(f"{label} is required.")
    if len(clean) > limit:
        raise ValueError(f"{label} exceeds limit {limit}.")
    return clean


def _optional(value: str | None, limit: int) -> str | None:
    return _required(value, "Value", limit) if value is not None and str(value).strip() else None


def _add_column(connection: sqlite3.Connection, name: str, definition: str) -> None:
    columns = {str(row["name"]) for row in connection.execute("PRAGMA table_info(native_coding_jobs)")}
    if name not in columns:
        connection.execute(f"ALTER TABLE native_coding_jobs ADD COLUMN {name} {definition}")
