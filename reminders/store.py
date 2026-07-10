from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class ReminderStatus(str, Enum):
    PENDING = "pending"
    SENDING = "sending"
    SENT = "sent"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass(frozen=True)
class Reminder:
    id: int
    user_id: int
    text: str
    remind_at: datetime
    status: ReminderStatus = ReminderStatus.PENDING
    recurrence: str | None = None


@dataclass
class InMemoryReminderStore:
    _items: list[Reminder] = field(default_factory=list)
    _next_id: int = 1

    def add(self, user_id: int, text: str, remind_at: datetime, *, recurrence: str | None = None) -> Reminder:
        item = Reminder(
            id=self._next_id,
            user_id=user_id,
            text=text.strip(),
            remind_at=remind_at,
            recurrence=recurrence,
        )
        self._next_id += 1
        self._items.append(item)
        return item

    def list_pending_for_user(self, user_id: int, *, limit: int = 10) -> list[Reminder]:
        items = [
            item
            for item in self._items
            if item.user_id == user_id and item.status == ReminderStatus.PENDING
        ]
        items.sort(key=lambda item: item.remind_at)
        return items[:limit]

    def cancel_for_user(self, user_id: int, reminder_id: int) -> bool:
        for index, item in enumerate(self._items):
            if item.id == reminder_id and item.user_id == user_id and item.status == ReminderStatus.PENDING:
                self._items[index] = Reminder(
                    id=item.id,
                    user_id=item.user_id,
                    text=item.text,
                    remind_at=item.remind_at,
                    status=ReminderStatus.CANCELLED,
                    recurrence=item.recurrence,
                )
                return True
        return False
