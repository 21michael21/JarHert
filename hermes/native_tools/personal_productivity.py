from __future__ import annotations

import sqlite3
from calendar import monthrange
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .database import open_personal_os_database
from .validation import allowed, optional, required


RECURRENCES = frozenset({"daily", "weekly", "monthly"})


@dataclass(frozen=True)
class PersonalReminder:
    id: int
    text: str
    remind_at: str
    recurrence: str | None
    status: str
    source_type: str | None
    source_id: int | None
    created_at: str
    updated_at: str


class PersonalProductivityStore:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path).expanduser()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def create_reminder(
        self,
        *,
        text: str,
        remind_at: str,
        idempotency_key: str,
        recurrence: str | None = None,
        source_type: str | None = None,
        source_id: int | None = None,
    ) -> PersonalReminder:
        key = required(idempotency_key, "Idempotency key", limit=220)
        clean_recurrence = _recurrence(recurrence)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM personal_reminders WHERE idempotency_key = ?",
                (key,),
            ).fetchone()
            if existing is not None:
                connection.commit()
                return _reminder_from_row(existing)
            reminder_id = int(
                connection.execute(
                    """
                    INSERT INTO personal_reminders(
                        text, remind_at, recurrence, status, idempotency_key,
                        source_type, source_id, updated_at
                    ) VALUES (?, ?, ?, 'active', ?, ?, ?, CURRENT_TIMESTAMP)
                    """,
                    (
                        required(text, "Reminder text", limit=1000),
                        _utc_timestamp(remind_at),
                        clean_recurrence,
                        key,
                        optional(source_type, limit=40),
                        int(source_id) if source_id is not None else None,
                    ),
                ).lastrowid
            )
            row = connection.execute(
                "SELECT * FROM personal_reminders WHERE id = ?", (reminder_id,)
            ).fetchone()
            connection.commit()
        return _reminder_from_row(row)

    def list_reminders(self, *, status: str = "active", limit: int = 100) -> list[PersonalReminder]:
        clean_status = allowed(status, frozenset({"active", "sent", "cancelled"}), "Reminder status")
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM personal_reminders WHERE status = ?
                ORDER BY remind_at, id LIMIT ?
                """,
                (clean_status, max(1, min(int(limit), 200))),
            ).fetchall()
        return [_reminder_from_row(row) for row in rows]

    def reminders_between(self, *, start: str, end: str) -> list[PersonalReminder]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM personal_reminders
                WHERE status = 'active' AND remind_at >= ? AND remind_at < ?
                  AND (source_type IS NULL OR source_type NOT IN ('commitment', 'crm_interaction'))
                ORDER BY remind_at, id
                """,
                (_utc_timestamp(start), _utc_timestamp(end)),
            ).fetchall()
        return [_reminder_from_row(row) for row in rows]

    def reschedule_reminder(
        self,
        reminder_id: int,
        *,
        remind_at: str,
        recurrence: str | None = "keep",
    ) -> PersonalReminder:
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT recurrence FROM personal_reminders WHERE id = ?",
                (int(reminder_id),),
            ).fetchone()
            clean_recurrence = (
                str(existing["recurrence"]) if existing and existing["recurrence"] else None
            ) if recurrence == "keep" else _recurrence(recurrence)
            cursor = connection.execute(
                """
                UPDATE personal_reminders
                SET remind_at = ?, recurrence = ?, status = 'active', updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status IN ('active', 'sent')
                """,
                (_utc_timestamp(remind_at), clean_recurrence, int(reminder_id)),
            )
            if cursor.rowcount != 1:
                raise ValueError("Напоминание не найдено или уже отменено.")
            row = connection.execute(
                "SELECT * FROM personal_reminders WHERE id = ?", (int(reminder_id),)
            ).fetchone()
        return _reminder_from_row(row)

    def cancel_reminder(self, reminder_id: int) -> PersonalReminder:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE personal_reminders
                SET status = 'cancelled', updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status = 'active'
                """,
                (int(reminder_id),),
            )
            if cursor.rowcount != 1:
                raise ValueError("Активное напоминание не найдено.")
            row = connection.execute(
                "SELECT * FROM personal_reminders WHERE id = ?", (int(reminder_id),)
            ).fetchone()
        return _reminder_from_row(row)

    def cancel_source_reminder(self, *, source_type: str, source_id: int) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE personal_reminders
                SET status = 'cancelled', updated_at = CURRENT_TIMESTAMP
                WHERE source_type = ? AND source_id = ? AND status = 'active'
                """,
                (required(source_type, "Source type", limit=40), int(source_id)),
            )

    def sync_source_reminder(
        self,
        *,
        source_type: str,
        source_id: int,
        text: str,
        remind_at: str,
        idempotency_key: str,
    ) -> PersonalReminder:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id FROM personal_reminders WHERE source_type = ? AND source_id = ?",
                (required(source_type, "Source type", limit=40), int(source_id)),
            ).fetchone()
            if row is not None:
                connection.execute(
                    """
                    UPDATE personal_reminders SET text = ?, remind_at = ?, status = 'active',
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (required(text, "Reminder text", limit=1000), _utc_timestamp(remind_at), int(row["id"])),
                )
                updated = connection.execute(
                    "SELECT * FROM personal_reminders WHERE id = ?", (int(row["id"]),)
                ).fetchone()
                return _reminder_from_row(updated)
        return self.create_reminder(
            text=text,
            remind_at=remind_at,
            idempotency_key=idempotency_key,
            source_type=source_type,
            source_id=source_id,
        )

    def claim_due_reminders(
        self,
        *,
        now: str | datetime | None = None,
        limit: int = 20,
    ) -> list[PersonalReminder]:
        current = _as_utc_datetime(now).isoformat()
        # updated_at хранится в формате CURRENT_TIMESTAMP ('YYYY-MM-DD HH:MM:SS'),
        # поэтому cutoff приводим к тому же виду для корректного сравнения строк.
        stale_cutoff = (_as_utc_datetime(now) - timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            # A dispatcher that died mid-send leaves rows in 'sending' forever;
            # after ten minutes the reminder is safe to claim again.
            connection.execute(
                """
                UPDATE personal_reminders
                SET status = 'active', updated_at = CURRENT_TIMESTAMP
                WHERE status = 'sending' AND updated_at <= ?
                """,
                (stale_cutoff,),
            )
            rows = connection.execute(
                """
                SELECT id FROM personal_reminders
                WHERE status = 'active' AND remind_at <= ?
                ORDER BY remind_at, id LIMIT ?
                """,
                (current, max(1, min(int(limit), 100))),
            ).fetchall()
            ids = [int(row["id"]) for row in rows]
            if ids:
                placeholders = ",".join("?" for _ in ids)
                connection.execute(
                    f"""
                    UPDATE personal_reminders
                    SET status = 'sending', attempts = attempts + 1, updated_at = CURRENT_TIMESTAMP
                    WHERE id IN ({placeholders})
                    """,
                    ids,
                )
            claimed = [
                _reminder_from_row(
                    connection.execute("SELECT * FROM personal_reminders WHERE id = ?", (item_id,)).fetchone()
                )
                for item_id in ids
            ]
            connection.commit()
        return claimed

    def mark_reminder_delivered(
        self,
        reminder_id: int,
        *,
        now: str | datetime | None = None,
    ) -> PersonalReminder:
        current = _as_utc_datetime(now)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM personal_reminders WHERE id = ? AND status = 'sending'",
                (int(reminder_id),),
            ).fetchone()
            if row is None:
                raise ValueError("Отправляемое напоминание не найдено.")
            recurrence = str(row["recurrence"]) if row["recurrence"] else None
            if recurrence:
                next_at = _next_occurrence(_as_utc_datetime(str(row["remind_at"])), recurrence, current)
                connection.execute(
                    """
                    UPDATE personal_reminders
                    SET status = 'active', remind_at = ?, last_error = NULL,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (next_at.isoformat(), int(reminder_id)),
                )
            else:
                connection.execute(
                    """
                    UPDATE personal_reminders
                    SET status = 'sent', last_error = NULL, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (int(reminder_id),),
                )
            updated = connection.execute(
                "SELECT * FROM personal_reminders WHERE id = ?", (int(reminder_id),)
            ).fetchone()
        return _reminder_from_row(updated)

    def release_failed_reminder(self, reminder_id: int, *, error: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE personal_reminders
                SET status = 'active', last_error = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status = 'sending'
                """,
                (str(error or "delivery failed")[:500], int(reminder_id)),
            )

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS personal_reminders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    text TEXT NOT NULL,
                    remind_at TEXT NOT NULL,
                    recurrence TEXT,
                    status TEXT NOT NULL DEFAULT 'active',
                    idempotency_key TEXT NOT NULL UNIQUE,
                    source_type TEXT,
                    source_id INTEGER,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS ix_personal_reminders_due
                    ON personal_reminders(status, remind_at);
                CREATE UNIQUE INDEX IF NOT EXISTS ux_personal_reminders_source
                    ON personal_reminders(source_type, source_id)
                    WHERE source_type IS NOT NULL AND source_id IS NOT NULL;
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return open_personal_os_database(self.database_path, autocommit=True)


def _reminder_from_row(row: sqlite3.Row) -> PersonalReminder:
    return PersonalReminder(
        id=int(row["id"]),
        text=str(row["text"]),
        remind_at=str(row["remind_at"]),
        recurrence=str(row["recurrence"]) if row["recurrence"] else None,
        status=str(row["status"]),
        source_type=str(row["source_type"]) if row["source_type"] else None,
        source_id=int(row["source_id"]) if row["source_id"] is not None else None,
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _utc_timestamp(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("Время напоминания должно быть ISO timestamp с timezone.") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Время напоминания должно содержать timezone.")
    return parsed.astimezone(timezone.utc).isoformat()


def local_day_bounds(now: str | None, timezone_name: str) -> tuple[str, str]:
    try:
        local_zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as error:
        raise ValueError("Неизвестный timezone.") from error
    if now:
        try:
            current = datetime.fromisoformat(now.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("Current time должен быть ISO timestamp с timezone.") from error
        if current.tzinfo is None or current.utcoffset() is None:
            raise ValueError("Current time должен содержать timezone.")
        current = current.astimezone(local_zone)
    else:
        current = datetime.now(local_zone)
    start = current.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    return start.astimezone(timezone.utc).isoformat(), end.astimezone(timezone.utc).isoformat()


def _as_utc_datetime(value: str | datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("Time должен быть ISO timestamp с timezone.") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Time должен содержать timezone.")
    return parsed.astimezone(timezone.utc)


def _next_occurrence(previous: datetime, recurrence: str, current: datetime) -> datetime:
    candidate = previous
    while candidate <= current:
        if recurrence == "daily":
            candidate += timedelta(days=1)
        elif recurrence == "weekly":
            candidate += timedelta(days=7)
        else:
            year = candidate.year + (1 if candidate.month == 12 else 0)
            month = 1 if candidate.month == 12 else candidate.month + 1
            candidate = candidate.replace(
                year=year,
                month=month,
                day=min(candidate.day, monthrange(year, month)[1]),
            )
    return candidate


def _recurrence(value: str | None) -> str | None:
    if value is None or not str(value).strip() or str(value).casefold() == "none":
        return None
    return allowed(str(value), RECURRENCES, "Recurrence")
