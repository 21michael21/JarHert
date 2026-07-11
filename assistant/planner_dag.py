from __future__ import annotations

from dataclasses import dataclass, field

from assistant.action_queue import AgentAction, ActionStatus
from assistant.action_schema import ActionType
from assistant.agent_jobs import AgentJob


@dataclass(frozen=True)
class PlanNode:
    key: str
    action_type: ActionType
    payload: dict[str, str]
    depends_on: tuple[str, ...] = field(default_factory=tuple)


class PlannerDag:
    def __init__(self, *, jobs, actions) -> None:
        self.jobs = jobs
        self.actions = actions

    def create_plan(
        self,
        *,
        user_id: int,
        goal: str,
        nodes: list[PlanNode],
        trace_id: str = "",
        idempotency_key: str | None = None,
    ) -> AgentJob:
        if not nodes:
            raise ValueError("Planner DAG must contain at least one node")
        _validate_nodes(nodes)
        job = self.jobs.create(
            user_id,
            goal,
            [f"{node.key}: {node.action_type.value}" for node in nodes],
            trace_id=trace_id,
            idempotency_key=idempotency_key,
        )
        action_by_key: dict[str, AgentAction] = {}
        for node in nodes:
            dependency_id = action_by_key[node.depends_on[-1]].id if node.depends_on else None
            action_by_key[node.key] = self.actions.enqueue(
                user_id=user_id,
                job_id=job.id,
                action_type=node.action_type,
                payload=node.payload,
                trace_id=trace_id,
                depends_on_action_id=dependency_id,
                idempotency_key=f"{idempotency_key}:{node.key}" if idempotency_key else None,
            )
        return job

    def pause(self, *, user_id: int, job_id: int) -> AgentJob:
        job = self._require_job(user_id, job_id)
        pause_job = getattr(self.actions, "pause_job_for_user", None)
        if pause_job is not None:
            pause_job(user_id, job_id)
        return self.jobs.mark_status(job.id, "paused")

    def resume(self, *, user_id: int, job_id: int) -> AgentJob:
        job = self._require_job(user_id, job_id)
        resume_job = getattr(self.actions, "resume_job_for_user", None)
        if resume_job is not None:
            resume_job(user_id, job_id)
        return self.jobs.mark_status(job.id, "queued")

    def cancel(self, *, user_id: int, job_id: int) -> AgentJob:
        job = self._require_job(user_id, job_id)
        cancel_job = getattr(self.actions, "cancel_job_for_user", None)
        if cancel_job is not None:
            cancel_job(user_id, job_id)
        return self.jobs.mark_status(job.id, "cancelled")

    def checkpoints(self, *, user_id: int, job_id: int) -> list[dict[str, object]]:
        self._require_job(user_id, job_id)
        items = [
            item
            for item in self.actions.list_for_user(user_id, limit=500)
            if item.job_id == job_id and item.status == ActionStatus.SUCCEEDED
        ]
        return [
            {
                "action_id": item.id,
                "type": item.type.value,
                "result_meta": dict(item.result_meta),
                "result_text": item.result_text,
            }
            for item in sorted(items, key=lambda value: value.id)
        ]

    def partial_results(self, *, user_id: int, job_id: int) -> list[str]:
        return [
            str(item["result_text"])
            for item in self.checkpoints(user_id=user_id, job_id=job_id)
            if item.get("result_text")
        ]

    def compensation_candidates(self, *, job_id: int, failed_action_id: int) -> list[AgentAction]:
        return self.actions.mark_compensation_skipped_for_job(job_id, failed_action_id, "manual rollback required")

    def _require_job(self, user_id: int, job_id: int) -> AgentJob:
        job = self.jobs.get_for_user(user_id, job_id)
        if job is None:
            raise KeyError(f"job not found: {job_id}")
        return job


def _validate_nodes(nodes: list[PlanNode]) -> None:
    keys = [node.key for node in nodes]
    if len(keys) != len(set(keys)):
        raise ValueError("Planner DAG node keys must be unique")
    seen: set[str] = set()
    for node in nodes:
        if not node.key:
            raise ValueError("Planner DAG node key is required")
        missing = [key for key in node.depends_on if key not in seen]
        if missing:
            raise ValueError(f"Planner DAG dependency must point to an earlier node: {missing[0]}")
        seen.add(node.key)
