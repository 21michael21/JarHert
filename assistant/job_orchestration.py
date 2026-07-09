from __future__ import annotations

from dataclasses import dataclass

from assistant.action_queue import ActionStatus, AgentAction


COMPENSATION_NONE = "none"
COMPENSATION_NOT_SUPPORTED = "not_supported"


@dataclass(frozen=True)
class JobStatusSummary:
    status: str
    total: int
    succeeded: int = 0
    failed: int = 0
    blocked: int = 0
    running: int = 0
    queued: int = 0
    needs_confirmation: int = 0
    cancelled: int = 0
    compensation_not_supported: int = 0
    next_action_id: int | None = None

    @property
    def progress_text(self) -> str:
        return f"{self.succeeded}/{self.total}" if self.total else "0/0"


def compute_job_status(actions: list[AgentAction]) -> JobStatusSummary:
    ordered = sorted(actions, key=lambda action: (action.created_at, action.id))
    total = len(ordered)
    counts = {status: 0 for status in ActionStatus}
    compensation_not_supported = 0
    next_action_id: int | None = None

    for action in ordered:
        counts[action.status] += 1
        if action.compensation_status == COMPENSATION_NOT_SUPPORTED:
            compensation_not_supported += 1
        if next_action_id is None and action.status in {
            ActionStatus.NEEDS_CONFIRMATION,
            ActionStatus.QUEUED,
            ActionStatus.RUNNING,
        }:
            next_action_id = action.id

    failed = counts[ActionStatus.FAILED]
    blocked = counts[ActionStatus.BLOCKED]
    cancelled = counts[ActionStatus.CANCELLED]
    succeeded = counts[ActionStatus.SUCCEEDED]
    running = counts[ActionStatus.RUNNING]
    queued = counts[ActionStatus.QUEUED]
    needs_confirmation = counts[ActionStatus.NEEDS_CONFIRMATION]

    if total == 0:
        status = "queued"
    elif running:
        status = "running"
    elif failed or blocked:
        status = "partial_failure" if succeeded else "failed"
    elif needs_confirmation:
        status = "needs_confirmation"
    elif queued:
        status = "queued"
    elif cancelled == total:
        status = "cancelled"
    elif succeeded == total:
        status = "succeeded"
    elif cancelled and succeeded:
        status = "partial_cancelled"
    else:
        status = "partial"

    return JobStatusSummary(
        status=status,
        total=total,
        succeeded=succeeded,
        failed=failed,
        blocked=blocked,
        running=running,
        queued=queued,
        needs_confirmation=needs_confirmation,
        cancelled=cancelled,
        compensation_not_supported=compensation_not_supported,
        next_action_id=next_action_id,
    )


def dependency_error(action: AgentAction, dependency: AgentAction | None) -> str | None:
    if action.depends_on_action_id is None:
        return None
    if dependency is None:
        return f"Dependency action #{action.depends_on_action_id} is missing."
    if dependency.status == ActionStatus.SUCCEEDED:
        return None
    if dependency.status in {ActionStatus.FAILED, ActionStatus.BLOCKED, ActionStatus.CANCELLED}:
        return f"Dependency action #{dependency.id} is {dependency.status.value}."
    return ""
