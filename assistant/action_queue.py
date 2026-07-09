from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum

from assistant.action_schema import ActionType


class ActionStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    NEEDS_CONFIRMATION = "needs_confirmation"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class AgentAction:
    id: int
    user_id: int
    type: ActionType
    payload: dict[str, str]
    status: ActionStatus = ActionStatus.QUEUED
    attempts: int = 0
    job_id: int | None = None
    trace_id: str = ""
    idempotency_key: str | None = None
    last_error: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class InMemoryActionQueueStore:
    def __init__(self) -> None:
        self._items: list[AgentAction] = []
        self._next_id = 1

    def enqueue(
        self,
        *,
        user_id: int,
        action_type: ActionType,
        payload: dict[str, str],
        job_id: int | None = None,
        trace_id: str = "",
        idempotency_key: str | None = None,
        status: ActionStatus = ActionStatus.QUEUED,
    ) -> AgentAction:
        if idempotency_key:
            existing = self._find_by_idempotency(user_id, idempotency_key)
            if existing is not None:
                return existing
        now = datetime.now(timezone.utc)
        item = AgentAction(
            id=self._next_id,
            user_id=user_id,
            job_id=job_id,
            trace_id=trace_id,
            type=action_type,
            payload=dict(payload),
            status=status,
            idempotency_key=idempotency_key,
            created_at=now,
            updated_at=now,
        )
        self._next_id += 1
        self._items.append(item)
        return item

    def list_for_user(self, user_id: int, *, limit: int = 20) -> list[AgentAction]:
        items = [item for item in self._items if item.user_id == user_id]
        return sorted(items, key=lambda item: (item.created_at, item.id), reverse=True)[:limit]

    def claim_next(self) -> AgentAction | None:
        for item in sorted(self._items, key=lambda item: (item.created_at, item.id)):
            if item.status != ActionStatus.QUEUED:
                continue
            updated = replace(
                item,
                status=ActionStatus.RUNNING,
                attempts=item.attempts + 1,
                updated_at=datetime.now(timezone.utc),
            )
            self._replace(updated)
            return updated
        return None

    def mark_succeeded(self, action_id: int) -> AgentAction:
        item = self._require(action_id)
        updated = replace(
            item,
            status=ActionStatus.SUCCEEDED,
            last_error=None,
            updated_at=datetime.now(timezone.utc),
        )
        self._replace(updated)
        return updated

    def mark_failed(self, action_id: int, error: str) -> AgentAction:
        item = self._require(action_id)
        updated = replace(
            item,
            status=ActionStatus.FAILED,
            last_error=_truncate_error(error),
            updated_at=datetime.now(timezone.utc),
        )
        self._replace(updated)
        return updated

    def retry_failed(self, action_id: int) -> AgentAction:
        item = self._require(action_id)
        updated = replace(
            item,
            status=ActionStatus.QUEUED,
            updated_at=datetime.now(timezone.utc),
        )
        self._replace(updated)
        return updated

    def cancel_for_user(self, user_id: int, action_id: int) -> bool:
        for item in self._items:
            if item.id == action_id and item.user_id == user_id and item.status in {
                ActionStatus.QUEUED,
                ActionStatus.NEEDS_CONFIRMATION,
            }:
                self._replace(
                    replace(item, status=ActionStatus.CANCELLED, updated_at=datetime.now(timezone.utc))
                )
                return True
        return False

    def confirm_for_user(self, user_id: int, action_id: int) -> AgentAction | None:
        for item in self._items:
            if item.id == action_id and item.user_id == user_id and item.status == ActionStatus.NEEDS_CONFIRMATION:
                updated = replace(item, status=ActionStatus.QUEUED, updated_at=datetime.now(timezone.utc))
                self._replace(updated)
                return updated
        return None

    def _find_by_idempotency(self, user_id: int, idempotency_key: str) -> AgentAction | None:
        for item in self._items:
            if item.user_id == user_id and item.idempotency_key == idempotency_key:
                return item
        return None

    def _require(self, action_id: int) -> AgentAction:
        for item in self._items:
            if item.id == action_id:
                return item
        raise KeyError(f"action not found: {action_id}")

    def _replace(self, updated: AgentAction) -> None:
        for index, item in enumerate(self._items):
            if item.id == updated.id:
                self._items[index] = updated
                return
        raise KeyError(f"action not found: {updated.id}")


def _truncate_error(error: str, *, limit: int = 1000) -> str:
    value = str(error).strip()
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"
