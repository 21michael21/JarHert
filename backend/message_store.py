from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from backend.models import CollectedMessageRecord


@dataclass(frozen=True)
class CollectedMessage:
    id: int
    chat_id: int
    chat_title: str | None
    sender_id: int | None
    sender_name: str | None
    text: str
    timestamp: datetime
    is_processed: bool = False
    telegram_message_id: int | None = None


class SqlCollectedMessageStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def add_message(
        self,
        *,
        chat_id: int,
        chat_title: str | None,
        sender_id: int | None,
        sender_name: str | None,
        text: str,
        timestamp: datetime,
        telegram_message_id: int | None = None,
    ) -> CollectedMessage:
        with self.session_factory() as db:
            if telegram_message_id is not None:
                existing = db.scalar(
                    select(CollectedMessageRecord).where(
                        CollectedMessageRecord.chat_id == chat_id,
                        CollectedMessageRecord.telegram_message_id == telegram_message_id,
                    )
                )
                if existing is not None:
                    return _message_from_record(existing)
            record = CollectedMessageRecord(
                chat_id=chat_id,
                chat_title=_trim(chat_title, 250),
                sender_id=sender_id,
                sender_name=_trim(sender_name, 250),
                text=text or "",
                timestamp=timestamp,
                telegram_message_id=telegram_message_id,
                is_processed=False,
            )
            db.add(record)
            db.commit()
            db.refresh(record)
            return _message_from_record(record)

    def list_unprocessed(
        self,
        *,
        since: datetime | None = None,
        limit: int = 200,
    ) -> list[CollectedMessage]:
        with self.session_factory() as db:
            query = select(CollectedMessageRecord).where(CollectedMessageRecord.is_processed.is_(False))
            if since is not None:
                query = query.where(CollectedMessageRecord.timestamp >= since)
            records = db.scalars(
                query.order_by(CollectedMessageRecord.timestamp.asc(), CollectedMessageRecord.id.asc()).limit(limit)
            ).all()
            return [_message_from_record(record) for record in records]

    def mark_processed(self, message_ids: list[int]) -> int:
        ids = [message_id for message_id in message_ids if message_id > 0]
        if not ids:
            return 0
        with self.session_factory() as db:
            result = db.execute(
                update(CollectedMessageRecord)
                .where(CollectedMessageRecord.id.in_(ids))
                .values(is_processed=True)
            )
            db.commit()
            return int(result.rowcount or 0)

    def count_unprocessed(self) -> int:
        with self.session_factory() as db:
            return len(db.scalars(select(CollectedMessageRecord.id).where(CollectedMessageRecord.is_processed.is_(False))).all())


def _message_from_record(record: CollectedMessageRecord) -> CollectedMessage:
    return CollectedMessage(
        id=record.id,
        chat_id=record.chat_id,
        chat_title=record.chat_title,
        sender_id=record.sender_id,
        sender_name=record.sender_name,
        text=record.text,
        timestamp=record.timestamp,
        is_processed=record.is_processed,
        telegram_message_id=record.telegram_message_id,
    )


def _trim(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    clean = str(value).strip()
    if not clean:
        return None
    return clean[:limit]
