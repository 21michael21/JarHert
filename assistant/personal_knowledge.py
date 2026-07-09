from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone


@dataclass(frozen=True)
class Note:
    id: int
    user_id: int
    text: str
    note_type: str = "note"
    source: str = "telegram"
    project: str | None = None
    contact: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    deleted_at: datetime | None = None
    expires_at: datetime | None = None


@dataclass(frozen=True)
class NoteHistory:
    id: int
    note_id: int
    user_id: int
    action: str
    before_text: str | None = None
    after_text: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class InMemoryPersonalKnowledgeStore:
    def __init__(self, *, default_retention_days: int | None = None) -> None:
        self.default_retention_days = default_retention_days
        self._notes: list[Note] = []
        self._history: list[NoteHistory] = []
        self._next_note_id = 1
        self._next_history_id = 1

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
        clean_text = _clean(text)
        days = retention_days if retention_days is not None else self.default_retention_days
        expires_at = created_at + timedelta(days=days) if days is not None else None
        note = Note(
            id=self._next_note_id,
            user_id=user_id,
            text=clean_text,
            note_type=_clean(note_type) or "note",
            source=_clean(source) or "telegram",
            project=_optional(project),
            contact=_optional(contact),
            created_at=created_at,
            updated_at=created_at,
            expires_at=expires_at,
        )
        self._next_note_id += 1
        self._notes.append(note)
        self._append_history(note, "create", before_text=None, after_text=note.text, now=created_at)
        return note

    def list_for_user(
        self,
        user_id: int,
        *,
        limit: int = 10,
        note_type: str | None = None,
        include_deleted: bool = False,
    ) -> list[Note]:
        notes = [
            note
            for note in self._notes
            if note.user_id == user_id
            and (include_deleted or note.deleted_at is None)
            and (note_type is None or note.note_type == note_type)
        ]
        notes.sort(key=lambda note: (note.updated_at, note.id), reverse=True)
        return notes[:limit]

    def search(self, user_id: int, query: str, *, limit: int = 10) -> list[Note]:
        needle = _clean(query).casefold()
        if not needle:
            return self.list_for_user(user_id, limit=limit)
        matches = [
            note
            for note in self.list_for_user(user_id, limit=10_000)
            if needle in _search_blob(note).casefold()
        ]
        return matches[:limit]

    def get(self, user_id: int, note_id: int) -> Note | None:
        for note in self._notes:
            if note.id == note_id and note.user_id == user_id and note.deleted_at is None:
                return note
        return None

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
        current = self.get(user_id, note_id)
        if current is None:
            return None
        updated_at = _aware(now)
        updated = replace(
            current,
            text=_clean(text) if text is not None else current.text,
            note_type=_clean(note_type) if note_type is not None else current.note_type,
            source=_clean(source) if source is not None else current.source,
            project=_optional(project) if project is not None else current.project,
            contact=_optional(contact) if contact is not None else current.contact,
            updated_at=updated_at,
        )
        self._replace_note(updated)
        self._append_history(updated, "update", before_text=current.text, after_text=updated.text, now=updated_at)
        return updated

    def delete(self, user_id: int, note_id: int, *, now: datetime | None = None) -> bool:
        current = self.get(user_id, note_id)
        if current is None:
            return False
        deleted_at = _aware(now)
        deleted = replace(current, deleted_at=deleted_at, updated_at=deleted_at)
        self._replace_note(deleted)
        self._append_history(deleted, "delete", before_text=current.text, after_text=None, now=deleted_at)
        return True

    def history_for_note(self, user_id: int, note_id: int) -> list[NoteHistory]:
        return [
            item
            for item in self._history
            if item.user_id == user_id and item.note_id == note_id
        ]

    def purge_expired(self, *, now: datetime | None = None) -> int:
        value = _aware(now)
        purged = 0
        for note in list(self._notes):
            if note.deleted_at is None and note.expires_at is not None and note.expires_at <= value:
                if self.delete(note.user_id, note.id, now=value):
                    purged += 1
        return purged

    def _replace_note(self, updated: Note) -> None:
        self._notes = [updated if note.id == updated.id else note for note in self._notes]

    def _append_history(
        self,
        note: Note,
        action: str,
        *,
        before_text: str | None,
        after_text: str | None,
        now: datetime,
    ) -> None:
        self._history.append(
            NoteHistory(
                id=self._next_history_id,
                note_id=note.id,
                user_id=note.user_id,
                action=action,
                before_text=before_text,
                after_text=after_text,
                created_at=now,
            )
        )
        self._next_history_id += 1


def _search_blob(note: Note) -> str:
    return " ".join(
        value or ""
        for value in (
            note.text,
            note.note_type,
            note.source,
            note.project,
            note.contact,
        )
    )


def _clean(value: str | None) -> str:
    return (value or "").strip()


def _optional(value: str | None) -> str | None:
    clean = _clean(value)
    return clean or None


def _aware(value: datetime | None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        return current.replace(tzinfo=timezone.utc)
    return current
