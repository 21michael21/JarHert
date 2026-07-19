from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .database import open_personal_os_database
from .validation import allowed, required


TRIP_STATUSES = frozenset({"active", "completed", "cancelled"})
TRIP_ITEM_KINDS = frozenset({"route", "booking", "document", "checklist"})
TRIP_ITEM_STATUSES = frozenset({"open", "done", "cancelled"})


@dataclass(frozen=True)
class Trip:
    id: int
    name: str
    destination: str
    starts_at: str | None
    ends_at: str | None
    status: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class TripItem:
    id: int
    trip_id: int
    kind: str
    title: str
    details: str | None
    due_at: str | None
    status: str
    created_at: str
    updated_at: str
    completed_at: str | None


class TripStore:
    """Keep a small factual workspace for a trip and its time-sensitive items."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path).expanduser()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def create(
        self,
        *,
        name: str,
        destination: str,
        idempotency_key: str,
        starts_at: str | None = None,
        ends_at: str | None = None,
    ) -> Trip:
        key = required(idempotency_key, "Idempotency key", limit=220)
        start = _utc_timestamp(starts_at) if starts_at else None
        end = _utc_timestamp(ends_at) if ends_at else None
        if start and end and start >= end:
            raise ValueError("Окончание поездки должно быть позже начала.")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute("SELECT * FROM trips WHERE idempotency_key = ?", (key,)).fetchone()
            if existing is not None:
                return _trip_from_row(existing)
            trip_id = int(
                connection.execute(
                    """
                    INSERT INTO trips(name, destination, starts_at, ends_at, status, idempotency_key)
                    VALUES (?, ?, ?, ?, 'active', ?)
                    """,
                    (
                        required(name, "Название поездки", limit=200),
                        required(destination, "Направление", limit=200),
                        start,
                        end,
                        key,
                    ),
                ).lastrowid
            )
            row = connection.execute("SELECT * FROM trips WHERE id = ?", (trip_id,)).fetchone()
        return _trip_from_row(row)

    def list(self, *, status: str = "active", limit: int = 100) -> list[Trip]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM trips WHERE status = ?
                ORDER BY starts_at IS NULL, starts_at, id DESC LIMIT ?
                """,
                (allowed(status, TRIP_STATUSES, "Статус поездки"), max(1, min(int(limit), 200))),
            ).fetchall()
        return [_trip_from_row(row) for row in rows]

    def get(self, trip_id: int) -> Trip:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM trips WHERE id = ?", (int(trip_id),)).fetchone()
        if row is None:
            raise ValueError("Поездка не найдена.")
        return _trip_from_row(row)

    def add_item(
        self,
        *,
        trip_id: int,
        kind: str,
        title: str,
        idempotency_key: str,
        details: str | None = None,
        due_at: str | None = None,
    ) -> TripItem:
        key = required(idempotency_key, "Idempotency key", limit=220)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            trip = connection.execute("SELECT status FROM trips WHERE id = ?", (int(trip_id),)).fetchone()
            if trip is None:
                raise ValueError("Поездка не найдена.")
            if str(trip["status"]) != "active":
                raise ValueError("Нельзя добавлять пункты в неактивную поездку.")
            existing = connection.execute("SELECT * FROM trip_items WHERE idempotency_key = ?", (key,)).fetchone()
            if existing is not None:
                return _item_from_row(existing)
            item_id = int(
                connection.execute(
                    """
                    INSERT INTO trip_items(trip_id, kind, title, details, due_at, status, idempotency_key)
                    VALUES (?, ?, ?, ?, ?, 'open', ?)
                    """,
                    (
                        int(trip_id),
                        allowed(kind, TRIP_ITEM_KINDS, "Тип пункта"),
                        required(title, "Пункт поездки", limit=240),
                        _optional(details, limit=4000),
                        _utc_timestamp(due_at) if due_at else None,
                        key,
                    ),
                ).lastrowid
            )
            row = connection.execute("SELECT * FROM trip_items WHERE id = ?", (item_id,)).fetchone()
        return _item_from_row(row)

    def list_items(self, trip_id: int, *, include_cancelled: bool = True) -> list[TripItem]:
        self.get(trip_id)
        query = "SELECT * FROM trip_items WHERE trip_id = ?"
        values: list[object] = [int(trip_id)]
        if not include_cancelled:
            query += " AND status != 'cancelled'"
        query += " ORDER BY id"
        with self._connect() as connection:
            rows = connection.execute(query, values).fetchall()
        return [_item_from_row(row) for row in rows]

    def complete_item(self, item_id: int) -> TripItem:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM trip_items WHERE id = ?", (int(item_id),)).fetchone()
            if row is None:
                raise ValueError("Пункт поездки не найден.")
            current = _item_from_row(row)
            if current.status == "done":
                return current
            if current.status != "open":
                raise ValueError("Отменённый пункт нельзя завершить.")
            connection.execute(
                """
                UPDATE trip_items SET status = 'done', completed_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP WHERE id = ?
                """,
                (int(item_id),),
            )
            updated = connection.execute("SELECT * FROM trip_items WHERE id = ?", (int(item_id),)).fetchone()
        return _item_from_row(updated)

    def cancel(self, trip_id: int) -> Trip:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM trips WHERE id = ?", (int(trip_id),)).fetchone()
            if row is None:
                raise ValueError("Поездка не найдена.")
            current = _trip_from_row(row)
            if current.status == "cancelled":
                return current
            connection.execute(
                "UPDATE trips SET status = 'cancelled', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (int(trip_id),),
            )
            connection.execute(
                """
                UPDATE trip_items SET status = 'cancelled', updated_at = CURRENT_TIMESTAMP
                WHERE trip_id = ? AND status = 'open'
                """,
                (int(trip_id),),
            )
            updated = connection.execute("SELECT * FROM trips WHERE id = ?", (int(trip_id),)).fetchone()
        return _trip_from_row(updated)

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS trips (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    destination TEXT NOT NULL,
                    starts_at TEXT,
                    ends_at TEXT,
                    status TEXT NOT NULL DEFAULT 'active',
                    idempotency_key TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS trip_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trip_id INTEGER NOT NULL REFERENCES trips(id) ON DELETE CASCADE,
                    kind TEXT NOT NULL,
                    title TEXT NOT NULL,
                    details TEXT,
                    due_at TEXT,
                    status TEXT NOT NULL DEFAULT 'open',
                    idempotency_key TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    completed_at TEXT
                );
                CREATE INDEX IF NOT EXISTS ix_trips_status_start ON trips(status, starts_at);
                CREATE INDEX IF NOT EXISTS ix_trip_items_trip_status ON trip_items(trip_id, status, id);
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return open_personal_os_database(self.database_path)


def _trip_from_row(row: sqlite3.Row) -> Trip:
    return Trip(
        id=int(row["id"]),
        name=str(row["name"]),
        destination=str(row["destination"]),
        starts_at=str(row["starts_at"]) if row["starts_at"] else None,
        ends_at=str(row["ends_at"]) if row["ends_at"] else None,
        status=str(row["status"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _item_from_row(row: sqlite3.Row) -> TripItem:
    return TripItem(
        id=int(row["id"]),
        trip_id=int(row["trip_id"]),
        kind=str(row["kind"]),
        title=str(row["title"]),
        details=str(row["details"]) if row["details"] else None,
        due_at=str(row["due_at"]) if row["due_at"] else None,
        status=str(row["status"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        completed_at=str(row["completed_at"]) if row["completed_at"] else None,
    )


def _utc_timestamp(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("Время поездки должно быть ISO timestamp с timezone.") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Время поездки должно содержать timezone.")
    return parsed.astimezone(timezone.utc).isoformat()


def _optional(value: str | None, *, limit: int) -> str | None:
    return required(value, "Значение", limit=limit) if value is not None and str(value).strip() else None
