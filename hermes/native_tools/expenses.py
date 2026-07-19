"""Personal one-off expenses: small, durable, idempotent records with monthly totals."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .database import open_personal_os_database
from .validation import optional, required


@dataclass(frozen=True)
class Expense:
    id: int
    text: str
    amount: float
    currency: str
    category: str | None
    project: str | None
    spent_at: str
    idempotency_key: str
    created_at: str


class ExpenseStore:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path).expanduser()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def add(
        self,
        *,
        text: str,
        amount: float,
        currency: str = "RUB",
        category: str | None = None,
        project: str | None = None,
        spent_at: str | None = None,
        idempotency_key: str,
    ) -> Expense:
        clean_text = required(text, "Трата", limit=200)
        clean_currency = required(currency, "Валюта", limit=10).upper()
        if not 0.01 <= float(amount) <= 100_000_000:
            raise ValueError("Сумма должна быть от 0.01 до 100000000.")
        clean_category = optional(category, limit=60)
        clean_project = optional(project, limit=120)
        clean_spent_at = optional(spent_at, limit=40)
        key = required(idempotency_key, "Idempotency key", limit=220)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT id FROM expenses WHERE idempotency_key = ?", (key,)
            ).fetchone()
            if existing is not None:
                connection.commit()
                return self._get(connection, int(existing["id"]))
            expense_id = int(
                connection.execute(
                    """
                    INSERT INTO expenses(text, amount, currency, category, project, spent_at, idempotency_key)
                    VALUES (?, ?, ?, ?, ?, COALESCE(?, datetime('now')), ?)
                    """,
                    (clean_text, round(float(amount), 2), clean_currency, clean_category, clean_project, clean_spent_at, key),
                ).lastrowid
            )
            connection.commit()
            return self._get(connection, expense_id)

    def list(self, *, limit: int = 50, category: str | None = None) -> list[Expense]:
        bounded = max(1, min(int(limit), 200))
        with self._connect() as connection:
            if category:
                rows = connection.execute(
                    "SELECT * FROM expenses WHERE category = ? ORDER BY spent_at DESC, id DESC LIMIT ?",
                    (str(category).strip(), bounded),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM expenses ORDER BY spent_at DESC, id DESC LIMIT ?",
                    (bounded,),
                ).fetchall()
        return [_from_row(row) for row in rows]

    def monthly_totals(self, *, month: str | None = None) -> dict[str, Any]:
        """Totals per currency for one month (YYYY-MM); default is the current UTC month."""
        clean_month = str(month or "").strip()
        with self._connect() as connection:
            if clean_month:
                rows = connection.execute(
                    """
                    SELECT currency, category, ROUND(SUM(amount), 2) AS total, COUNT(*) AS count
                    FROM expenses WHERE strftime('%Y-%m', spent_at) = ?
                    GROUP BY currency, category ORDER BY currency, total DESC
                    """,
                    (clean_month,),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT currency, category, ROUND(SUM(amount), 2) AS total, COUNT(*) AS count
                    FROM expenses WHERE strftime('%Y-%m', spent_at) = strftime('%Y-%m', 'now')
                    GROUP BY currency, category ORDER BY currency, total DESC
                    """
                ).fetchall()
        return {
            "month": clean_month or None,
            "items": [
                {"currency": str(row["currency"]), "category": row["category"], "total": float(row["total"]), "count": int(row["count"])}
                for row in rows
            ],
        }

    def _get(self, connection: sqlite3.Connection, expense_id: int) -> Expense:
        row = connection.execute("SELECT * FROM expenses WHERE id = ?", (expense_id,)).fetchone()
        return _from_row(row)

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS expenses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    text TEXT NOT NULL,
                    amount REAL NOT NULL,
                    currency TEXT NOT NULL DEFAULT 'RUB',
                    category TEXT,
                    project TEXT,
                    spent_at TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS ix_expenses_spent_at ON expenses(spent_at);
                CREATE INDEX IF NOT EXISTS ix_expenses_category ON expenses(category);
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return open_personal_os_database(self.database_path)


def _from_row(row: sqlite3.Row) -> Expense:
    return Expense(
        id=int(row["id"]),
        text=str(row["text"]),
        amount=float(row["amount"]),
        currency=str(row["currency"]),
        category=str(row["category"]) if row["category"] else None,
        project=str(row["project"]) if row["project"] else None,
        spent_at=str(row["spent_at"]),
        idempotency_key=str(row["idempotency_key"]),
        created_at=str(row["created_at"]),
    )
