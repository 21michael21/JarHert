from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass(frozen=True)
class ConversationTurn:
    id: int
    user_id: int
    user_text: str
    assistant_text: str
    extracted_actions: list[dict]
    created_at: datetime


class InMemoryConversationStore:
    def __init__(self) -> None:
        self._items: list[ConversationTurn] = []
        self._next_id = 1

    def add(
        self,
        *,
        user_id: int,
        user_text: str,
        assistant_text: str,
        extracted_actions: list[dict] | None = None,
    ) -> ConversationTurn:
        item = ConversationTurn(
            id=self._next_id,
            user_id=user_id,
            user_text=(user_text or "").strip(),
            assistant_text=(assistant_text or "").strip(),
            extracted_actions=list(extracted_actions or []),
            created_at=datetime.now(timezone.utc),
        )
        self._next_id += 1
        self._items.append(item)
        return item

    def list_recent(self, user_id: int, *, limit: int = 10) -> list[ConversationTurn]:
        items = [item for item in self._items if item.user_id == user_id]
        return sorted(items, key=lambda item: (item.created_at, item.id), reverse=True)[:limit]

    def get_for_user(self, user_id: int, turn_id: int) -> ConversationTurn | None:
        return next(
            (item for item in self._items if item.user_id == user_id and item.id == turn_id),
            None,
        )

    def latest_user_text(self, user_id: int) -> str | None:
        for item in self.list_recent(user_id, limit=10):
            if _is_context_candidate(item.user_text):
                return item.user_text
        return None


def action_to_dict(action) -> dict:
    return {
        "type": action.type.value,
        "payload": dict(action.payload),
        "confidence": action.confidence,
        "needs_confirmation": action.needs_confirmation,
        "reason": action.reason,
    }


def actions_to_dicts(actions) -> list[dict]:
    return [action_to_dict(action) for action in actions]


def _is_context_candidate(text: str) -> bool:
    value = (text or "").strip().lower()
    if not value:
        return False
    return value not in {
        "запиши это как идею",
        "сохрани это как идею",
        "запиши это как важное",
        "сохрани это как важное",
    }
