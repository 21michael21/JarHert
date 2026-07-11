from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


INTERACTION_KINDS = frozenset({"message", "call", "meeting", "agreement", "note"})


@dataclass(frozen=True)
class CRMInteraction:
    id: int
    contact: str
    kind: str
    summary: str
    project: str | None
    occurred_at: str
    next_contact_at: str | None
    created_at: str


class PersonalCRMStore:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path).expanduser()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def log_interaction(
        self,
        *,
        contact: str,
        kind: str,
        summary: str,
        idempotency_key: str,
        project: str | None = None,
        occurred_at: str | None = None,
        next_contact_at: str | None = None,
    ) -> CRMInteraction:
        key = _required(idempotency_key, "Idempotency key", limit=220)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM crm_interactions WHERE idempotency_key = ?", (key,)
            ).fetchone()
            if existing is not None:
                connection.commit()
                return _interaction_from_row(existing)
            interaction_id = int(
                connection.execute(
                    """
                    INSERT INTO crm_interactions(
                        contact, kind, summary, project_key, occurred_at,
                        next_contact_at, idempotency_key
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        _required(contact, "Contact", limit=160),
                        _allowed(kind, INTERACTION_KINDS, "Interaction kind"),
                        _required(summary, "Summary", limit=2000),
                        _optional(project, limit=120),
                        _utc_timestamp(occurred_at) if occurred_at else datetime.now(timezone.utc).isoformat(),
                        _utc_timestamp(next_contact_at) if next_contact_at else None,
                        key,
                    ),
                ).lastrowid
            )
            row = connection.execute(
                "SELECT * FROM crm_interactions WHERE id = ?", (interaction_id,)
            ).fetchone()
            connection.commit()
        return _interaction_from_row(row)

    def list_interactions(
        self,
        *,
        contact: str | None = None,
        project: str | None = None,
        limit: int = 100,
    ) -> list[CRMInteraction]:
        clauses: list[str] = []
        values: list[object] = []
        if contact:
            clauses.append("contact = ? COLLATE NOCASE")
            values.append(_required(contact, "Contact", limit=160))
        if project:
            clauses.append("project_key = ? COLLATE NOCASE")
            values.append(_required(project, "Project", limit=120))
        query = "SELECT * FROM crm_interactions"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY occurred_at DESC, id DESC LIMIT ?"
        values.append(max(1, min(int(limit), 200)))
        with self._connect() as connection:
            rows = connection.execute(query, values).fetchall()
        return [_interaction_from_row(row) for row in rows]

    def followups_between(self, *, start: str, end: str) -> list[CRMInteraction]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM crm_interactions
                WHERE next_contact_at >= ? AND next_contact_at < ?
                ORDER BY next_contact_at, id
                """,
                (_utc_timestamp(start), _utc_timestamp(end)),
            ).fetchall()
        return [_interaction_from_row(row) for row in rows]

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS crm_interactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    contact TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    project_key TEXT,
                    occurred_at TEXT NOT NULL,
                    next_contact_at TEXT,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS ix_crm_interactions_contact_time
                    ON crm_interactions(contact, occurred_at);
                CREATE INDEX IF NOT EXISTS ix_crm_interactions_followup
                    ON crm_interactions(next_contact_at) WHERE next_contact_at IS NOT NULL;
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 10000")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection


def _interaction_from_row(row: sqlite3.Row) -> CRMInteraction:
    return CRMInteraction(
        id=int(row["id"]),
        contact=str(row["contact"]),
        kind=str(row["kind"]),
        summary=str(row["summary"]),
        project=str(row["project_key"]) if row["project_key"] else None,
        occurred_at=str(row["occurred_at"]),
        next_contact_at=str(row["next_contact_at"]) if row["next_contact_at"] else None,
        created_at=str(row["created_at"]),
    )


def _utc_timestamp(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("Время CRM должно быть ISO timestamp с timezone.") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Время CRM должно содержать timezone.")
    return parsed.astimezone(timezone.utc).isoformat()


def _allowed(value: str, allowed: frozenset[str], label: str) -> str:
    clean = _required(value, label, limit=40).casefold()
    if clean not in allowed:
        raise ValueError(f"{label} отсутствует в allowlist.")
    return clean


def _required(value: str, label: str, *, limit: int) -> str:
    clean = " ".join(str(value or "").split())
    if not clean:
        raise ValueError(f"{label} не должен быть пустым.")
    if len(clean) > limit:
        raise ValueError(f"{label} превышает лимит {limit} символов.")
    return clean


def _optional(value: str | None, *, limit: int) -> str | None:
    if value is None or not str(value).strip():
        return None
    return _required(value, "Value", limit=limit)
