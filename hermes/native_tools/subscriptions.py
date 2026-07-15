from __future__ import annotations

import sqlite3
import json
import os
import shlex
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any

from .database import open_personal_os_database


CADENCES = frozenset({"weekly", "monthly", "yearly"})


def subscription_sync_from_env():
    raw = os.getenv("SUBSCRIPTION_SYNC_COMMAND", "").strip()
    if not raw:
        return None
    argv = shlex.split(raw)
    if not argv:
        return None

    def sync(rows: list[dict[str, Any]]) -> None:
        result = subprocess.run(
            argv,
            input=json.dumps(rows, ensure_ascii=False),
            capture_output=True,
            check=False,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout or "subscription sync failed")[:500])

    return sync


@dataclass(frozen=True)
class Subscription:
    id: int
    name: str
    amount: str
    currency: str
    cadence: str
    next_charge_at: str
    category: str | None
    status: str
    created_at: str
    updated_at: str


class SubscriptionStore:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path).expanduser()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def create(self, **payload: Any) -> tuple[Subscription, bool]:
        key = _required(payload.get("idempotency_key"), "Idempotency key", limit=220)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM subscriptions WHERE idempotency_key = ?", (key,)
            ).fetchone()
            if existing is not None:
                connection.commit()
                return _from_row(existing), False
            subscription_id = int(
                connection.execute(
                    """
                    INSERT INTO subscriptions(
                        name, amount, currency, cadence, next_charge_at,
                        category, status, idempotency_key, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 'active', ?, CURRENT_TIMESTAMP)
                    """,
                    (
                        _required(payload.get("name"), "Name", limit=160),
                        _amount(payload.get("amount")),
                        _currency(payload.get("currency")),
                        _cadence(payload.get("cadence")),
                        _utc_timestamp(payload.get("next_charge_at")),
                        _optional(payload.get("category"), limit=80),
                        key,
                    ),
                ).lastrowid
            )
            row = connection.execute("SELECT * FROM subscriptions WHERE id = ?", (subscription_id,)).fetchone()
            connection.commit()
        return _from_row(row), True

    def list(self, *, status: str = "active") -> list[Subscription]:
        clean_status = _allowed(status, {"active", "cancelled"}, "Status")
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM subscriptions WHERE status = ? ORDER BY next_charge_at, id",
                (clean_status,),
            ).fetchall()
        return [_from_row(row) for row in rows]

    def update(
        self,
        subscription_id: int,
        *,
        amount: str | None = None,
        cadence: str | None = None,
        next_charge_at: str | None = None,
        category: str | None = None,
    ) -> Subscription:
        current = self.get(subscription_id)
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE subscriptions SET amount = ?, cadence = ?, next_charge_at = ?,
                    category = ?, status = 'active', updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    _amount(amount) if amount is not None else current.amount,
                    _cadence(cadence) if cadence is not None else current.cadence,
                    _utc_timestamp(next_charge_at) if next_charge_at is not None else current.next_charge_at,
                    _optional(category, limit=80) if category is not None else current.category,
                    int(subscription_id),
                ),
            )
        return self.get(subscription_id)

    def cancel(self, subscription_id: int) -> Subscription:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE subscriptions SET status = 'cancelled', updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status = 'active'
                """,
                (int(subscription_id),),
            )
            if cursor.rowcount != 1:
                raise ValueError("Активная подписка не найдена.")
        return self.get(subscription_id)

    def get(self, subscription_id: int) -> Subscription:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM subscriptions WHERE id = ?", (int(subscription_id),)).fetchone()
        if row is None:
            raise ValueError("Подписка не найдена.")
        return _from_row(row)

    def monthly_totals(self) -> dict[str, str]:
        totals: dict[str, Decimal] = {}
        for item in self.list():
            amount = Decimal(item.amount)
            monthly = amount if item.cadence == "monthly" else amount / 12 if item.cadence == "yearly" else amount * 52 / 12
            totals[item.currency] = totals.get(item.currency, Decimal("0")) + monthly
        return {currency: _decimal(value) for currency, value in sorted(totals.items())}

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS subscriptions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    amount TEXT NOT NULL,
                    currency TEXT NOT NULL,
                    cadence TEXT NOT NULL,
                    next_charge_at TEXT NOT NULL,
                    category TEXT,
                    status TEXT NOT NULL DEFAULT 'active',
                    idempotency_key TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return open_personal_os_database(self.database_path, autocommit=True)


def _from_row(row: sqlite3.Row) -> Subscription:
    return Subscription(**{field: row[field] for field in Subscription.__dataclass_fields__})


def _amount(value: Any) -> str:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ValueError("Amount должен быть числом.") from error
    if amount <= 0:
        raise ValueError("Amount должен быть больше нуля.")
    return _decimal(amount)


def _decimal(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _currency(value: Any) -> str:
    clean = _required(value, "Currency", limit=3).upper()
    if len(clean) != 3 or not clean.isalpha():
        raise ValueError("Currency должен быть трёхбуквенным кодом.")
    return clean


def _cadence(value: Any) -> str:
    return _allowed(value, CADENCES, "Cadence")


def _utc_timestamp(value: Any) -> str:
    parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Дата списания должна содержать timezone.")
    return parsed.astimezone(timezone.utc).isoformat()


def _allowed(value: Any, allowed: set[str] | frozenset[str], label: str) -> str:
    clean = _required(value, label, limit=40).casefold()
    if clean not in allowed:
        raise ValueError(f"{label} отсутствует в allowlist.")
    return clean


def _required(value: Any, label: str, *, limit: int) -> str:
    clean = " ".join(str(value or "").split())
    if not clean:
        raise ValueError(f"{label} не должен быть пустым.")
    if len(clean) > limit:
        raise ValueError(f"{label} превышает лимит {limit} символов.")
    return clean


def _optional(value: Any, *, limit: int) -> str | None:
    return _required(value, "Value", limit=limit) if value is not None and str(value).strip() else None
