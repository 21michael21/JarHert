from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone


@dataclass(frozen=True)
class Contact:
    id: int
    user_id: int
    name: str
    aliases: tuple[str, ...] = ()
    tg_user_id: int | None = None
    chat_id: int | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class InMemoryContactBookStore:
    def __init__(self) -> None:
        self._items: list[Contact] = []
        self._next_id = 1

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
        normalized_aliases = _normalize_aliases(clean_name, aliases or ())
        existing = self.resolve(user_id, clean_name)
        now = datetime.now(timezone.utc)
        if existing is None:
            contact = Contact(
                id=self._next_id,
                user_id=user_id,
                name=clean_name,
                aliases=normalized_aliases,
                tg_user_id=tg_user_id,
                chat_id=chat_id if chat_id is not None else tg_user_id,
                created_at=now,
                updated_at=now,
            )
            self._next_id += 1
            self._items.append(contact)
            return contact
        updated = replace(
            existing,
            name=clean_name,
            aliases=normalized_aliases,
            tg_user_id=tg_user_id,
            chat_id=chat_id if chat_id is not None else tg_user_id,
            updated_at=now,
        )
        self._items = [updated if item.id == updated.id else item for item in self._items]
        return updated

    def resolve(self, user_id: int, value: str) -> Contact | None:
        normalized = normalize_contact_key(value)
        if not normalized:
            return None
        for contact in self._items:
            if contact.user_id != user_id:
                continue
            keys = {normalize_contact_key(contact.name), *(normalize_contact_key(alias) for alias in contact.aliases)}
            if normalized in keys:
                return contact
        return None

    def list_for_user(self, user_id: int, *, limit: int = 50) -> list[Contact]:
        contacts = [contact for contact in self._items if contact.user_id == user_id]
        contacts.sort(key=lambda item: (item.name.casefold(), item.id))
        return contacts[:limit]


def normalize_contact_key(value: str | None) -> str:
    clean = (value or "").strip().casefold()
    if clean.startswith("@"):
        clean = clean[1:]
    return " ".join(clean.split())


def _clean_name(value: str) -> str:
    clean = " ".join((value or "").strip().split())
    if not clean:
        raise ValueError("contact name is required")
    return clean


def _normalize_aliases(name: str, aliases: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    values = []
    seen = {normalize_contact_key(name)}
    for alias in aliases:
        clean = " ".join((alias or "").strip().split())
        key = normalize_contact_key(clean)
        if not clean or key in seen:
            continue
        seen.add(key)
        values.append(clean)
    return tuple(values)
