from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, sessionmaker

from assistant.personal_knowledge import Note, NoteHistory
from backend.models import NoteHistoryRecord, NoteRecord
from backend.store_converters import note_from_record, note_history_from_record


class SqlPersonalKnowledgeStore:
    def __init__(self, session_factory: sessionmaker[Session], *, default_retention_days: int | None = None) -> None:
        self.session_factory = session_factory
        self.default_retention_days = default_retention_days

    def create(
        self,
        *,
        user_id: int,
        text: str,
        note_type: str = "note",
        source: str = "telegram",
        project: str | None = None,
        contact: str | None = None,
        now: datetime | None = None,
        retention_days: int | None = None,
    ) -> Note:
        created_at = _aware(now)
        days = retention_days if retention_days is not None else self.default_retention_days
        expires_at = created_at + timedelta(days=days) if days is not None else None
        with self.session_factory() as db:
            record = NoteRecord(
                user_id=user_id,
                text=(text or "").strip(),
                type=(note_type or "note").strip() or "note",
                source=(source or "telegram").strip() or "telegram",
                project=_optional(project),
                contact=_optional(contact),
                status="active",
                created_at=created_at,
                updated_at=created_at,
                expires_at=expires_at,
            )
            db.add(record)
            db.flush()
            db.add(
                NoteHistoryRecord(
                    note_id=record.id,
                    user_id=user_id,
                    action="create",
                    before_text=None,
                    after_text=record.text,
                    created_at=created_at,
                )
            )
            db.commit()
            db.refresh(record)
            return note_from_record(record)

    def list_for_user(
        self,
        user_id: int,
        *,
        limit: int = 10,
        note_type: str | None = None,
        include_deleted: bool = False,
    ) -> list[Note]:
        with self.session_factory() as db:
            query = select(NoteRecord).where(NoteRecord.user_id == user_id)
            if not include_deleted:
                query = query.where(NoteRecord.status == "active")
            if note_type is not None:
                query = query.where(NoteRecord.type == note_type)
            records = db.scalars(
                query.order_by(NoteRecord.updated_at.desc(), NoteRecord.id.desc()).limit(limit)
            ).all()
            return [note_from_record(record) for record in records]

    def search(self, user_id: int, query: str, *, limit: int = 10) -> list[Note]:
        clean_query = (query or "").strip()
        if not clean_query:
            return self.list_for_user(user_id, limit=limit)
        pattern = f"%{clean_query.lower()}%"
        with self.session_factory() as db:
            records = db.scalars(
                select(NoteRecord)
                .where(
                    NoteRecord.user_id == user_id,
                    NoteRecord.status == "active",
                    or_(
                        NoteRecord.text.ilike(pattern),
                        NoteRecord.type.ilike(pattern),
                        NoteRecord.source.ilike(pattern),
                        NoteRecord.project.ilike(pattern),
                        NoteRecord.contact.ilike(pattern),
                    ),
                )
                .order_by(NoteRecord.updated_at.desc(), NoteRecord.id.desc())
                .limit(limit)
            ).all()
            return [note_from_record(record) for record in records]

    def get(self, user_id: int, note_id: int) -> Note | None:
        with self.session_factory() as db:
            record = db.scalar(
                select(NoteRecord).where(
                    NoteRecord.id == note_id,
                    NoteRecord.user_id == user_id,
                    NoteRecord.status == "active",
                )
            )
            return note_from_record(record) if record is not None else None

    def latest_for_user(self, user_id: int) -> Note | None:
        items = self.list_for_user(user_id, limit=1)
        return items[0] if items else None

    def update(
        self,
        user_id: int,
        note_id: int,
        *,
        text: str | None = None,
        note_type: str | None = None,
        source: str | None = None,
        project: str | None = None,
        contact: str | None = None,
        now: datetime | None = None,
    ) -> Note | None:
        updated_at = _aware(now)
        with self.session_factory() as db:
            record = db.scalar(
                select(NoteRecord).where(
                    NoteRecord.id == note_id,
                    NoteRecord.user_id == user_id,
                    NoteRecord.status == "active",
                )
            )
            if record is None:
                return None
            before_text = record.text
            if text is not None:
                record.text = text.strip()
            if note_type is not None:
                record.type = note_type.strip() or "note"
            if source is not None:
                record.source = source.strip() or "telegram"
            if project is not None:
                record.project = _optional(project)
            if contact is not None:
                record.contact = _optional(contact)
            record.updated_at = updated_at
            db.add(
                NoteHistoryRecord(
                    note_id=record.id,
                    user_id=user_id,
                    action="update",
                    before_text=before_text,
                    after_text=record.text,
                    created_at=updated_at,
                )
            )
            db.commit()
            db.refresh(record)
            return note_from_record(record)

    def delete(self, user_id: int, note_id: int, *, now: datetime | None = None) -> bool:
        deleted_at = _aware(now)
        with self.session_factory() as db:
            record = db.scalar(
                select(NoteRecord).where(
                    NoteRecord.id == note_id,
                    NoteRecord.user_id == user_id,
                    NoteRecord.status == "active",
                )
            )
            if record is None:
                return False
            before_text = record.text
            record.status = "deleted"
            record.deleted_at = deleted_at
            record.updated_at = deleted_at
            db.add(
                NoteHistoryRecord(
                    note_id=record.id,
                    user_id=user_id,
                    action="delete",
                    before_text=before_text,
                    after_text=None,
                    created_at=deleted_at,
                )
            )
            db.commit()
            return True

    def history_for_note(self, user_id: int, note_id: int) -> list[NoteHistory]:
        with self.session_factory() as db:
            records = db.scalars(
                select(NoteHistoryRecord)
                .where(
                    NoteHistoryRecord.user_id == user_id,
                    NoteHistoryRecord.note_id == note_id,
                )
                .order_by(NoteHistoryRecord.created_at.asc(), NoteHistoryRecord.id.asc())
            ).all()
            return [note_history_from_record(record) for record in records]

    def purge_expired(self, *, now: datetime | None = None) -> int:
        value = _aware(now)
        count = 0
        for note in self.list_expired(now=value):
            if self.delete(note.user_id, note.id, now=value):
                count += 1
        return count

    def list_expired(self, *, now: datetime | None = None, limit: int = 100) -> list[Note]:
        value = _aware(now)
        with self.session_factory() as db:
            records = db.scalars(
                select(NoteRecord)
                .where(
                    NoteRecord.status == "active",
                    NoteRecord.expires_at.is_not(None),
                    NoteRecord.expires_at <= value,
                )
                .order_by(NoteRecord.expires_at.asc(), NoteRecord.id.asc())
                .limit(limit)
            ).all()
            return [note_from_record(record) for record in records]


def _optional(value: str | None) -> str | None:
    clean = (value or "").strip()
    return clean or None


def _aware(value: datetime | None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        return current.replace(tzinfo=timezone.utc)
    return current
