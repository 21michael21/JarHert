from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from assistant.contact_book import Contact, normalize_contact_key
from backend.models import ContactAliasRecord, ContactRecord
from backend.store_converters import contact_from_record


class SqlContactBookStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def upsert(
        self,
        *,
        user_id: int,
        name: str,
        aliases: list[str] | tuple[str, ...] | None = None,
        tg_user_id: int | None = None,
        chat_id: int | None = None,
    ) -> Contact:
        clean_name = _clean_name(name)
        normalized_name = normalize_contact_key(clean_name)
        normalized_aliases = _clean_aliases(clean_name, aliases or ())
        now = datetime.now(timezone.utc)
        with self.session_factory() as db:
            record = db.scalar(
                select(ContactRecord).where(
                    ContactRecord.user_id == user_id,
                    ContactRecord.normalized_name == normalized_name,
                )
            )
            if record is None:
                record = ContactRecord(
                    user_id=user_id,
                    name=clean_name,
                    normalized_name=normalized_name,
                    tg_user_id=tg_user_id,
                    chat_id=chat_id if chat_id is not None else tg_user_id,
                    created_at=now,
                    updated_at=now,
                )
                db.add(record)
                db.flush()
            else:
                record.name = clean_name
                record.normalized_name = normalized_name
                record.tg_user_id = tg_user_id
                record.chat_id = chat_id if chat_id is not None else tg_user_id
                record.updated_at = now
                db.execute(delete(ContactAliasRecord).where(ContactAliasRecord.contact_id == record.id))
            for alias in normalized_aliases:
                db.add(
                    ContactAliasRecord(
                        contact_id=record.id,
                        user_id=user_id,
                        alias=alias,
                        normalized_alias=normalize_contact_key(alias),
                    )
                )
            db.commit()
            db.refresh(record)
            return self._from_record(db, record)

    def resolve(self, user_id: int, value: str) -> Contact | None:
        normalized = normalize_contact_key(value)
        if not normalized:
            return None
        with self.session_factory() as db:
            record = db.scalar(
                select(ContactRecord).where(
                    ContactRecord.user_id == user_id,
                    ContactRecord.normalized_name == normalized,
                )
            )
            if record is None:
                alias = db.scalar(
                    select(ContactAliasRecord).where(
                        ContactAliasRecord.user_id == user_id,
                        ContactAliasRecord.normalized_alias == normalized,
                    )
                )
                if alias is not None:
                    record = db.get(ContactRecord, alias.contact_id)
            return self._from_record(db, record) if record is not None else None

    def list_for_user(self, user_id: int, *, limit: int = 50) -> list[Contact]:
        with self.session_factory() as db:
            records = db.scalars(
                select(ContactRecord)
                .where(ContactRecord.user_id == user_id)
                .order_by(ContactRecord.normalized_name.asc(), ContactRecord.id.asc())
                .limit(limit)
            ).all()
            return [self._from_record(db, record) for record in records]

    def _from_record(self, db: Session, record: ContactRecord) -> Contact:
        aliases = db.scalars(
            select(ContactAliasRecord)
            .where(ContactAliasRecord.contact_id == record.id)
            .order_by(ContactAliasRecord.id.asc())
        ).all()
        return contact_from_record(record, aliases)


def _clean_name(value: str) -> str:
    clean = " ".join((value or "").strip().split())
    if not clean:
        raise ValueError("contact name is required")
    return clean


def _clean_aliases(name: str, aliases: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    values: list[str] = []
    seen = {normalize_contact_key(name)}
    for alias in aliases:
        clean = " ".join((alias or "").strip().split())
        key = normalize_contact_key(clean)
        if not clean or key in seen:
            continue
        seen.add(key)
        values.append(clean)
    return tuple(values)
