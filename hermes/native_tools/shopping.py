from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path


_STATUSES = frozenset({"needed", "bought", "cancelled"})


@dataclass(frozen=True)
class ShoppingItem:
    id: int
    text: str
    category: str | None
    quantity: str | None
    project: str | None
    status: str
    created_at: str
    updated_at: str
    purchased_at: str | None


class ShoppingStore:
    """A lightweight shared shopping list in the Personal OS SQLite database."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path).expanduser()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def add(
        self,
        *,
        text: str,
        category: str | None = None,
        quantity: str | None = None,
        project: str | None = None,
        idempotency_key: str,
    ) -> ShoppingItem:
        clean_text = _required(text, "Покупка", limit=240)
        text_key = _key(clean_text)
        clean_project = _optional(project, limit=120)
        clean_key = _required(idempotency_key, "Idempotency key", limit=220)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM shopping_items WHERE idempotency_key = ?", (clean_key,)
            ).fetchone()
            if existing is not None:
                return _from_row(existing)
            duplicate = connection.execute(
                """
                SELECT * FROM shopping_items
                WHERE status = 'needed' AND text_key = ? AND project_key = ?
                ORDER BY id DESC LIMIT 1
                """,
                (text_key, clean_project or ""),
            ).fetchone()
            if duplicate is not None:
                return _from_row(duplicate)
            item_id = int(
                connection.execute(
                    """
                    INSERT INTO shopping_items(
                        text, text_key, category, quantity, project_key, status, idempotency_key
                    ) VALUES (?, ?, ?, ?, ?, 'needed', ?)
                    """,
                    (
                        clean_text,
                        text_key,
                        _optional(category, limit=80),
                        _optional(quantity, limit=80),
                        clean_project or "",
                        clean_key,
                    ),
                ).lastrowid
            )
            row = connection.execute("SELECT * FROM shopping_items WHERE id = ?", (item_id,)).fetchone()
        return _from_row(row)

    def list(self, *, status: str = "needed", project: str | None = None, limit: int = 100) -> list[ShoppingItem]:
        clean_status = _status(status)
        values: list[object] = [clean_status]
        clause = "status = ?"
        if project:
            clause += " AND project_key = ?"
            values.append(_required(project, "Проект", limit=120))
        values.append(max(1, min(int(limit), 200)))
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM shopping_items WHERE {clause} ORDER BY id DESC LIMIT ?", values
            ).fetchall()
        return [_from_row(row) for row in rows]

    def mark_bought(self, item_id: int) -> ShoppingItem:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM shopping_items WHERE id = ?", (int(item_id),)).fetchone()
            if row is None:
                raise ValueError("Позиция покупок не найдена.")
            current = _from_row(row)
            if current.status == "bought":
                return current
            if current.status != "needed":
                raise ValueError("Отменённую позицию нельзя отметить купленной.")
            connection.execute(
                """
                UPDATE shopping_items
                SET status = 'bought', purchased_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (int(item_id),),
            )
            updated = connection.execute("SELECT * FROM shopping_items WHERE id = ?", (int(item_id),)).fetchone()
        return _from_row(updated)

    def remove(self, item_id: int) -> ShoppingItem:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM shopping_items WHERE id = ?", (int(item_id),)).fetchone()
            if row is None:
                raise ValueError("Позиция покупок не найдена.")
            current = _from_row(row)
            if current.status == "cancelled":
                return current
            connection.execute(
                "UPDATE shopping_items SET status = 'cancelled', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (int(item_id),),
            )
            updated = connection.execute("SELECT * FROM shopping_items WHERE id = ?", (int(item_id),)).fetchone()
        return _from_row(updated)

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS shopping_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    text TEXT NOT NULL,
                    text_key TEXT NOT NULL,
                    category TEXT,
                    quantity TEXT,
                    project_key TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'needed',
                    idempotency_key TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    purchased_at TEXT
                );
                CREATE INDEX IF NOT EXISTS ix_shopping_items_status_project
                    ON shopping_items(status, project_key, id DESC);
                CREATE INDEX IF NOT EXISTS ix_shopping_items_active_key
                    ON shopping_items(status, text_key, project_key);
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 10000")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection


def _from_row(row: sqlite3.Row) -> ShoppingItem:
    return ShoppingItem(
        id=int(row["id"]),
        text=str(row["text"]),
        category=str(row["category"]) if row["category"] else None,
        quantity=str(row["quantity"]) if row["quantity"] else None,
        project=str(row["project_key"]) if row["project_key"] else None,
        status=str(row["status"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        purchased_at=str(row["purchased_at"]) if row["purchased_at"] else None,
    )


def _key(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def _status(value: str) -> str:
    clean = str(value or "").strip().casefold()
    if clean not in _STATUSES:
        raise ValueError("Статус должен быть needed, bought или cancelled.")
    return clean


def _required(value: str, label: str, *, limit: int) -> str:
    clean = str(value or "").strip()
    if not clean:
        raise ValueError(f"{label} не должна быть пустой.")
    if len(clean) > limit:
        raise ValueError(f"{label} превышает лимит {limit} символов.")
    return clean


def _optional(value: str | None, *, limit: int) -> str | None:
    return _required(value, "Значение", limit=limit) if value is not None and str(value).strip() else None
