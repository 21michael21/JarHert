from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from enum import Enum

from assistant.action_schema import ActionType
from assistant.automation_runtime import LeaseLostError


class ActionStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    NEEDS_CONFIRMATION = "needs_confirmation"
    CANCELLED = "cancelled"
    BLOCKED = "blocked"
    PAUSED = "paused"


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
    depends_on_action_id: int | None = None
    compensation_for_action_id: int | None = None
    compensation_status: str = "none"
    result_meta: dict[str, str] = field(default_factory=dict)
    result_text: str | None = None
    idempotency_key: str | None = None
    worker_id: str | None = None
    lease_until: datetime | None = None
    claimed_at: datetime | None = None
    heartbeat_at: datetime | None = None
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
        depends_on_action_id: int | None = None,
        compensation_for_action_id: int | None = None,
        compensation_status: str = "none",
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
            depends_on_action_id=depends_on_action_id,
            compensation_for_action_id=compensation_for_action_id,
            compensation_status=compensation_status,
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

    def claim_next(
        self,
        *,
        worker_id: str | None = None,
        now: datetime | None = None,
        lease_seconds: float = 75,
    ) -> AgentAction | None:
        claimed_at = now or datetime.now(timezone.utc)
        for item in sorted(self._items, key=lambda item: (item.created_at, item.id)):
            if item.status != ActionStatus.QUEUED:
                continue
            dependency_error = self._dependency_error(item)
            if dependency_error == "":
                continue
            if dependency_error:
                self._replace(
                    replace(
                        item,
                        status=ActionStatus.BLOCKED,
                        last_error=_truncate_error(dependency_error),
                        updated_at=datetime.now(timezone.utc),
                    )
                )
                continue
            updated = replace(
                item,
                status=ActionStatus.RUNNING,
                attempts=item.attempts + 1,
                worker_id=worker_id,
                claimed_at=claimed_at,
                heartbeat_at=claimed_at,
                lease_until=claimed_at + timedelta(seconds=lease_seconds),
                updated_at=claimed_at,
            )
            self._replace(updated)
            return updated
        return None

    def heartbeat(
        self,
        action_id: int,
        *,
        worker_id: str,
        now: datetime | None = None,
        lease_seconds: float = 75,
    ) -> bool:
        item = self._require(action_id)
        if item.status != ActionStatus.RUNNING or item.worker_id != worker_id:
            return False
        heartbeat_at = now or datetime.now(timezone.utc)
        self._replace(
            replace(
                item,
                heartbeat_at=heartbeat_at,
                lease_until=heartbeat_at + timedelta(seconds=lease_seconds),
                updated_at=heartbeat_at,
            )
        )
        return True

    def recover_expired(self, *, now: datetime | None = None) -> int:
        expired_at = now or datetime.now(timezone.utc)
        recovered = 0
        for item in list(self._items):
            if item.status != ActionStatus.RUNNING:
                continue
            if item.lease_until is not None and item.lease_until > expired_at:
                continue
            self._replace(
                replace(
                    item,
                    status=ActionStatus.QUEUED,
                    worker_id=None,
                    lease_until=None,
                    claimed_at=None,
                    heartbeat_at=None,
                    updated_at=expired_at,
                )
            )
            recovered += 1
        return recovered

    def recover_running(self) -> int:
        recovered = 0
        for item in list(self._items):
            if item.status != ActionStatus.RUNNING:
                continue
            self._replace(
                replace(
                    item,
                    status=ActionStatus.QUEUED,
                    worker_id=None,
                    lease_until=None,
                    claimed_at=None,
                    heartbeat_at=None,
                    updated_at=datetime.now(timezone.utc),
                )
            )
            recovered += 1
        return recovered

    def mark_succeeded(
        self,
        action_id: int,
        *,
        result_meta: dict[str, str] | None = None,
        result_text: str | None = None,
        worker_id: str | None = None,
    ) -> AgentAction:
        item = self._require(action_id)
        _assert_action_owner(item, worker_id)
        updated = replace(
            item,
            status=ActionStatus.SUCCEEDED,
            result_meta=dict(result_meta or item.result_meta),
            result_text=result_text if result_text is not None else item.result_text,
            lease_until=None,
            last_error=None,
            updated_at=datetime.now(timezone.utc),
        )
        self._replace(updated)
        self._unblock_dependents_after_success(updated.id)
        return updated

    def mark_failed(self, action_id: int, error: str, *, worker_id: str | None = None) -> AgentAction:
        item = self._require(action_id)
        _assert_action_owner(item, worker_id)
        updated = replace(
            item,
            status=ActionStatus.FAILED,
            lease_until=None,
            last_error=_truncate_error(error),
            updated_at=datetime.now(timezone.utc),
        )
        self._replace(updated)
        return updated

    def retry_failed(self, action_id: int, *, worker_id: str | None = None) -> AgentAction:
        item = self._require(action_id)
        _assert_action_owner(item, worker_id)
        updated = replace(
            item,
            status=ActionStatus.QUEUED,
            worker_id=None,
            lease_until=None,
            claimed_at=None,
            heartbeat_at=None,
            last_error=None,
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

    def confirm_job_for_user(self, user_id: int, job_id: int) -> list[AgentAction]:
        confirmed: list[AgentAction] = []
        for item in sorted(self._items, key=lambda value: (value.created_at, value.id)):
            if item.user_id != user_id or item.job_id != job_id or item.status != ActionStatus.NEEDS_CONFIRMATION:
                continue
            updated = replace(item, status=ActionStatus.QUEUED, updated_at=datetime.now(timezone.utc))
            self._replace(updated)
            confirmed.append(updated)
        return confirmed

    def cancel_job_for_user(self, user_id: int, job_id: int) -> list[AgentAction]:
        cancelled: list[AgentAction] = []
        for item in sorted(self._items, key=lambda value: (value.created_at, value.id)):
            if item.user_id != user_id or item.job_id != job_id:
                continue
            if item.status not in {ActionStatus.QUEUED, ActionStatus.NEEDS_CONFIRMATION, ActionStatus.PAUSED}:
                continue
            updated = replace(item, status=ActionStatus.CANCELLED, updated_at=datetime.now(timezone.utc))
            self._replace(updated)
            cancelled.append(updated)
        return cancelled

    def pause_job_for_user(self, user_id: int, job_id: int) -> list[AgentAction]:
        return self._change_job_status(user_id, job_id, ActionStatus.QUEUED, ActionStatus.PAUSED)

    def resume_job_for_user(self, user_id: int, job_id: int) -> list[AgentAction]:
        return self._change_job_status(user_id, job_id, ActionStatus.PAUSED, ActionStatus.QUEUED)

    def _change_job_status(
        self,
        user_id: int,
        job_id: int,
        current: ActionStatus,
        target: ActionStatus,
    ) -> list[AgentAction]:
        changed: list[AgentAction] = []
        for item in sorted(self._items, key=lambda value: (value.created_at, value.id)):
            if item.user_id != user_id or item.job_id != job_id or item.status != current:
                continue
            updated = replace(item, status=target, updated_at=datetime.now(timezone.utc))
            self._replace(updated)
            changed.append(updated)
        return changed

    def block_dependents(self, action_id: int, reason: str) -> list[AgentAction]:
        blocked: list[AgentAction] = []
        for item in sorted(self._items, key=lambda value: (value.created_at, value.id)):
            if item.depends_on_action_id != action_id:
                continue
            if item.status not in {ActionStatus.QUEUED, ActionStatus.NEEDS_CONFIRMATION}:
                continue
            updated = replace(
                item,
                status=ActionStatus.BLOCKED,
                last_error=_truncate_error(reason),
                updated_at=datetime.now(timezone.utc),
            )
            self._replace(updated)
            blocked.append(updated)
            blocked.extend(self.block_dependents(updated.id, reason))
        return blocked

    def mark_compensation_skipped_for_job(self, job_id: int, failed_action_id: int, reason: str) -> list[AgentAction]:
        updated_items: list[AgentAction] = []
        for item in sorted(self._items, key=lambda value: (value.created_at, value.id)):
            if item.job_id != job_id or item.id == failed_action_id:
                continue
            if item.status != ActionStatus.SUCCEEDED or item.compensation_status != "none":
                continue
            compensation_status = "available" if _has_external_result_ids(item.result_meta) else "not_supported"
            compensation_error = (
                "Rollback identifiers are available, but no safe rollback tool is configured."
                if compensation_status == "available"
                else reason
            )
            updated = replace(
                item,
                compensation_status=compensation_status,
                last_error=_truncate_error(compensation_error),
                updated_at=datetime.now(timezone.utc),
            )
            self._replace(updated)
            updated_items.append(updated)
        return updated_items

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

    def _find(self, action_id: int) -> AgentAction | None:
        for item in self._items:
            if item.id == action_id:
                return item
        return None

    def _dependency_error(self, item: AgentAction) -> str | None:
        if item.depends_on_action_id is None:
            return None
        dependency = self._find(item.depends_on_action_id)
        if dependency is None:
            return f"Dependency action #{item.depends_on_action_id} is missing."
        if dependency.status == ActionStatus.SUCCEEDED:
            return None
        if dependency.status in {ActionStatus.FAILED, ActionStatus.BLOCKED, ActionStatus.CANCELLED}:
            return f"Dependency action #{dependency.id} is {dependency.status.value}."
        return ""

    def _unblock_dependents_after_success(self, action_id: int) -> None:
        for item in list(self._items):
            if item.depends_on_action_id != action_id or item.status != ActionStatus.BLOCKED:
                continue
            self._replace(
                replace(
                    item,
                    status=ActionStatus.QUEUED,
                    last_error=None,
                    updated_at=datetime.now(timezone.utc),
                )
            )

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


def _assert_action_owner(item: AgentAction, worker_id: str | None) -> None:
    if worker_id is None:
        return
    if item.status != ActionStatus.RUNNING or item.worker_id != worker_id:
        raise LeaseLostError(f"action lease lost: action_id={item.id} worker_id={worker_id}")


def _has_external_result_ids(meta: dict[str, str]) -> bool:
    return any(key.endswith("_id") or key.endswith("_url") for key in meta)
