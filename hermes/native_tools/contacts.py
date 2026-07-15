from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .database import open_personal_os_database


class ContactStoreError(ValueError):
    pass


@dataclass(frozen=True)
class Contact:
    id: int
    name: str
    telegram_chat_id: int
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class ScheduledMessage:
    id: int
    plan_id: int
    contact_id: int
    contact_name: str
    telegram_chat_id: int
    text: str
    send_at: datetime
    status: str
    attempts: int = 0
    external_id: str | None = None
    sent_at: datetime | None = None
    last_error: str | None = None


@dataclass(frozen=True)
class MessagePlan:
    id: int
    status: str
    idempotency_key: str
    messages: tuple[ScheduledMessage, ...]


class ContactStore:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path).expanduser()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def add_contact(self, *, name: str, telegram_chat_id: int, aliases: list[str]) -> Contact:
        clean_name = _required_text(name, "Имя контакта")
        normalized_aliases = _unique_aliases([clean_name, *aliases])
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    "INSERT INTO contacts(name, telegram_chat_id) VALUES (?, ?)",
                    (clean_name, int(telegram_chat_id)),
                )
                contact_id = int(cursor.lastrowid)
                connection.executemany(
                    "INSERT INTO contact_aliases(contact_id, alias, normalized_alias) VALUES (?, ?, ?)",
                    [(contact_id, alias, _normalize(alias)) for alias in normalized_aliases],
                )
        except sqlite3.IntegrityError as error:
            raise ContactStoreError("Имя или alias уже используется другим контактом.") from error
        return self.get_contact(contact_id)

    def resolve_contact(self, value: str) -> Contact:
        normalized = _normalize(value)
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT c.* FROM contacts c
                JOIN contact_aliases a ON a.contact_id = c.id
                WHERE a.normalized_alias = ?
                """,
                (normalized,),
            ).fetchone()
        if row is None:
            raise ContactStoreError(f"Контакт «{value.strip()}» не найден.")
        return self._contact_from_row(row)

    def get_contact(self, contact_id: int) -> Contact:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM contacts WHERE id = ?", (contact_id,)).fetchone()
        if row is None:
            raise ContactStoreError("Контакт не найден.")
        return self._contact_from_row(row)

    def list_contacts(self) -> list[Contact]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM contacts ORDER BY name COLLATE NOCASE").fetchall()
        return [self._contact_from_row(row) for row in rows]

    def create_message_plan(
        self,
        items: list[dict[str, Any]],
        *,
        idempotency_key: str,
    ) -> MessagePlan:
        key = _required_text(idempotency_key, "Idempotency key")
        if not items or len(items) > 20:
            raise ContactStoreError("План должен содержать от 1 до 20 сообщений.")
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT id FROM message_plans WHERE idempotency_key = ?",
                (key,),
            ).fetchone()
            if existing is not None:
                return self._get_plan(connection, int(existing["id"]))
            connection.execute("BEGIN IMMEDIATE")
            plan_id = int(
                connection.execute(
                    "INSERT INTO message_plans(status, idempotency_key) VALUES ('draft', ?)",
                    (key,),
                ).lastrowid
            )
            for item in items:
                contact = self.resolve_contact(str(item.get("contact") or ""))
                text = _required_text(str(item.get("text") or ""), "Текст сообщения")
                send_at = _parse_datetime(str(item.get("send_at") or ""))
                connection.execute(
                    """
                    INSERT INTO scheduled_messages(plan_id, contact_id, text, send_at, status)
                    VALUES (?, ?, ?, ?, 'draft')
                    """,
                    (plan_id, contact.id, text, send_at.isoformat()),
                )
            connection.commit()
            return self._get_plan(connection, plan_id)

    def approve_message_plan(self, plan_id: int) -> MessagePlan:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT status FROM message_plans WHERE id = ?", (plan_id,)).fetchone()
            if row is None:
                raise ContactStoreError("План сообщений не найден.")
            if row["status"] == "draft":
                now = datetime.now(timezone.utc).isoformat()
                connection.execute(
                    "UPDATE message_plans SET status = 'scheduled', approved_at = ? WHERE id = ?",
                    (now, plan_id),
                )
                connection.execute(
                    "UPDATE scheduled_messages SET status = 'scheduled' WHERE plan_id = ? AND status = 'draft'",
                    (plan_id,),
                )
            elif row["status"] != "scheduled":
                raise ContactStoreError(f"План нельзя подтвердить в статусе {row['status']}.")
            connection.commit()
            return self._get_plan(connection, plan_id)

    def get_message_plan(self, plan_id: int) -> MessagePlan:
        with self._connect() as connection:
            return self._get_plan(connection, plan_id)

    def cancel_message_plan(self, plan_id: int) -> MessagePlan:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT status FROM message_plans WHERE id = ?", (plan_id,)).fetchone()
            if row is None:
                raise ContactStoreError("План сообщений не найден.")
            if row["status"] in {"draft", "scheduled"}:
                connection.execute(
                    "UPDATE message_plans SET status = 'cancelled' WHERE id = ?",
                    (plan_id,),
                )
                connection.execute(
                    """
                    UPDATE scheduled_messages SET status = 'cancelled'
                    WHERE plan_id = ? AND status IN ('draft', 'scheduled')
                    """,
                    (plan_id,),
                )
            elif row["status"] != "cancelled":
                raise ContactStoreError(f"План нельзя отменить в статусе {row['status']}.")
            connection.commit()
            return self._get_plan(connection, plan_id)

    def count_message_plans(self) -> int:
        with self._connect() as connection:
            return int(connection.execute("SELECT COUNT(*) FROM message_plans").fetchone()[0])

    def claim_due_messages(self, *, now: datetime | None = None, limit: int = 20) -> list[ScheduledMessage]:
        current = _ensure_aware(now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """
                SELECT id FROM scheduled_messages
                WHERE status = 'scheduled' AND send_at <= ?
                ORDER BY send_at, id LIMIT ?
                """,
                (current.isoformat(), max(1, min(limit, 100))),
            ).fetchall()
            ids = [int(row["id"]) for row in rows]
            if ids:
                placeholders = ",".join("?" for _ in ids)
                connection.execute(
                    f"UPDATE scheduled_messages SET status = 'sending', attempts = attempts + 1, claimed_at = ? WHERE id IN ({placeholders})",
                    (current.isoformat(), *ids),
                )
            connection.commit()
            return [self._get_message(connection, message_id) for message_id in ids]

    def get_message(self, message_id: int) -> ScheduledMessage:
        with self._connect() as connection:
            return self._get_message(connection, message_id)

    def mark_message_sent(self, message_id: int, *, external_id: str | None = None) -> ScheduledMessage:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE scheduled_messages
                SET status = 'sent', sent_at = ?, external_id = ?, last_error = NULL
                WHERE id = ? AND status = 'sending'
                """,
                (now, external_id, message_id),
            )
            connection.commit()
            return self._get_message(connection, message_id)

    def mark_message_failed(self, message_id: int, *, error: str) -> ScheduledMessage:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE scheduled_messages SET status = 'failed', last_error = ?
                WHERE id = ? AND status = 'sending'
                """,
                (error.strip()[:500], message_id),
            )
            connection.commit()
            return self._get_message(connection, message_id)

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS contacts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    telegram_chat_id INTEGER NOT NULL UNIQUE,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS contact_aliases (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    contact_id INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
                    alias TEXT NOT NULL,
                    normalized_alias TEXT NOT NULL UNIQUE
                );
                CREATE TABLE IF NOT EXISTS message_plans (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    status TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    approved_at TEXT
                );
                CREATE TABLE IF NOT EXISTS scheduled_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    plan_id INTEGER NOT NULL REFERENCES message_plans(id) ON DELETE CASCADE,
                    contact_id INTEGER NOT NULL REFERENCES contacts(id),
                    text TEXT NOT NULL,
                    send_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    claimed_at TEXT,
                    sent_at TEXT,
                    external_id TEXT,
                    last_error TEXT
                );
                CREATE INDEX IF NOT EXISTS ix_scheduled_messages_due
                    ON scheduled_messages(status, send_at);
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return open_personal_os_database(self.database_path, autocommit=True)

    def _contact_from_row(self, row: sqlite3.Row) -> Contact:
        with self._connect() as connection:
            aliases = connection.execute(
                "SELECT alias FROM contact_aliases WHERE contact_id = ? ORDER BY id",
                (row["id"],),
            ).fetchall()
        return Contact(
            id=int(row["id"]),
            name=str(row["name"]),
            telegram_chat_id=int(row["telegram_chat_id"]),
            aliases=tuple(str(item["alias"]) for item in aliases),
        )

    def _get_plan(self, connection: sqlite3.Connection, plan_id: int) -> MessagePlan:
        row = connection.execute("SELECT * FROM message_plans WHERE id = ?", (plan_id,)).fetchone()
        if row is None:
            raise ContactStoreError("План сообщений не найден.")
        messages = connection.execute(
            "SELECT id FROM scheduled_messages WHERE plan_id = ? ORDER BY id",
            (plan_id,),
        ).fetchall()
        return MessagePlan(
            id=int(row["id"]),
            status=str(row["status"]),
            idempotency_key=str(row["idempotency_key"]),
            messages=tuple(self._get_message(connection, int(item["id"])) for item in messages),
        )

    def _get_message(self, connection: sqlite3.Connection, message_id: int) -> ScheduledMessage:
        row = connection.execute(
            """
            SELECT m.*, c.name AS contact_name, c.telegram_chat_id
            FROM scheduled_messages m JOIN contacts c ON c.id = m.contact_id
            WHERE m.id = ?
            """,
            (message_id,),
        ).fetchone()
        if row is None:
            raise ContactStoreError("Сообщение не найдено.")
        return ScheduledMessage(
            id=int(row["id"]),
            plan_id=int(row["plan_id"]),
            contact_id=int(row["contact_id"]),
            contact_name=str(row["contact_name"]),
            telegram_chat_id=int(row["telegram_chat_id"]),
            text=str(row["text"]),
            send_at=_parse_datetime(str(row["send_at"])),
            status=str(row["status"]),
            attempts=int(row["attempts"]),
            external_id=str(row["external_id"]) if row["external_id"] else None,
            sent_at=_parse_datetime(str(row["sent_at"])) if row["sent_at"] else None,
            last_error=str(row["last_error"]) if row["last_error"] else None,
        )


def _required_text(value: str, field: str) -> str:
    clean = " ".join(value.split())
    if not clean:
        raise ContactStoreError(f"{field} не может быть пустым.")
    return clean


def _normalize(value: str) -> str:
    return " ".join(value.casefold().split())


def _unique_aliases(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        clean = _required_text(value, "Alias")
        normalized = _normalize(clean)
        if normalized not in seen:
            result.append(clean)
            seen.add(normalized)
    return result


def _parse_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ContactStoreError("Время должно быть ISO timestamp с timezone.") from error
    return _ensure_aware(parsed).astimezone(timezone.utc)


def _ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ContactStoreError("Время должно содержать timezone.")
    return value


def as_json(value: Contact | ScheduledMessage | MessagePlan) -> str:
    def convert(item: Any) -> Any:
        if isinstance(item, datetime):
            return item.isoformat()
        if isinstance(item, tuple):
            return [convert(child) for child in item]
        if hasattr(item, "__dataclass_fields__"):
            return {name: convert(getattr(item, name)) for name in item.__dataclass_fields__}
        return item

    return json.dumps(convert(value), ensure_ascii=False, separators=(",", ":"))
