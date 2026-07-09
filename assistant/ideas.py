from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass(frozen=True)
class Idea:
    id: int
    user_id: int
    text: str
    created_at: datetime


@dataclass
class InMemoryIdeaStore:
    _items: list[Idea] = field(default_factory=list)
    _next_id: int = 1

    def add(self, user_id: int, text: str) -> Idea:
        item = Idea(
            id=self._next_id,
            user_id=user_id,
            text=text.strip(),
            created_at=datetime.now(timezone.utc),
        )
        self._next_id += 1
        self._items.append(item)
        return item

    def list_for_user(self, user_id: int, *, limit: int = 10) -> list[Idea]:
        items = [item for item in self._items if item.user_id == user_id]
        return list(reversed(items))[:limit]
