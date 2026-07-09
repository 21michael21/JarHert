from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import replace

from assistant.action_queue import AgentAction
from assistant.automation_runtime import AutomationRuntime, InMemoryAutomationLeaseStore, LeaseLostError, WorkerPolicy
from assistant.tool_registry import ToolExecutionError, ToolExecutionResult


logger = logging.getLogger(__name__)


AsyncActionExecutor = Callable[[AgentAction], Awaitable[str | ToolExecutionResult]]
AsyncActionResultDelivery = Callable[[AgentAction, str], Awaitable[None]]
ActionEventLogger = Callable[[AgentAction, str, dict], None]
JobStatusUpdater = Callable[[AgentAction], None]


class ActionWorkerAdapter:
    name = "actions"
    default_policy = WorkerPolicy(interval_seconds=2, timeout_seconds=60, lease_seconds=90, heartbeat_seconds=15)

    def __init__(
        self,
        action_queue,
        execute: AsyncActionExecutor,
        deliver: AsyncActionResultDelivery,
        *,
        policy: WorkerPolicy | None = None,
        max_attempts: int = 3,
        event_logger: ActionEventLogger | None = None,
        job_status_updater: JobStatusUpdater | None = None,
        worker_id: str | None = None,
        item_lease_seconds: float = 75,
        item_heartbeat_seconds: float = 15,
    ) -> None:
        self.action_queue = action_queue
        self.execute = execute
        self.deliver = deliver
        self.policy = policy or self.default_policy
        self.max_attempts = max_attempts
        self.event_logger = event_logger
        self.job_status_updater = job_status_updater
        self.worker_id = worker_id or f"actions-{uuid.uuid4().hex[:12]}"
        self.item_lease_seconds = item_lease_seconds
        self.item_heartbeat_seconds = item_heartbeat_seconds
        if not 0 < item_heartbeat_seconds < item_lease_seconds:
            raise ValueError("action item heartbeat must be lower than item lease")

    async def recover_stale(self) -> int:
        recover = getattr(self.action_queue, "recover_running", None)
        return recover() if recover is not None else 0

    async def run_once(self) -> dict:
        recover_expired = getattr(self.action_queue, "recover_expired", None)
        recovered = recover_expired() if recover_expired is not None else 0
        action = self.action_queue.claim_next(
            worker_id=self.worker_id,
            lease_seconds=self.item_lease_seconds,
        )
        if action is None:
            return {"processed": 0, "recovered": recovered}
        lease_lost = asyncio.Event()
        heartbeat_task = asyncio.create_task(self._heartbeat_item(action.id, lease_lost))
        try:
            _log_event(self.event_logger, action, "action_started", {"attempts": action.attempts})
            try:
                result = _coerce_result(await self.execute(action))
            except Exception as error:
                if lease_lost.is_set():
                    return self._lease_lost(action)
                retryable = isinstance(error, ToolExecutionError) and error.retryable
                try:
                    if retryable and action.attempts < self.max_attempts:
                        self.action_queue.retry_failed(action.id, worker_id=self.worker_id)
                        _log_event(self.event_logger, action, "tool_failed", {"retryable": True, "error": str(error)})
                        return {"processed": 1, "retried": 1}
                    self.action_queue.mark_failed(action.id, str(error) or error.__class__.__name__, worker_id=self.worker_id)
                except LeaseLostError:
                    return self._lease_lost(action)
                _log_event(self.event_logger, action, "tool_failed", {"retryable": False, "error": str(error)})
                _block_downstream(self.action_queue, self.event_logger, action)
                _mark_compensation_skipped(self.action_queue, self.event_logger, action)
                _update_job_status(self.job_status_updater, action)
                await self.deliver(action, _failure_text(action, error))
                return {"processed": 1, "failed": 1}

            if lease_lost.is_set():
                return self._lease_lost(action)
            try:
                self.action_queue.mark_succeeded(
                    action.id,
                    result_meta=result.meta,
                    result_text=result.message,
                    worker_id=self.worker_id,
                )
            except LeaseLostError:
                return self._lease_lost(action)
            _log_event(
                self.event_logger,
                action,
                "action_succeeded",
                {"result_chars": len(result.message), "result_meta": dict(result.meta)},
            )
            _update_job_status(self.job_status_updater, action)
            await self.deliver(action, _success_text(action, result.message))
            return {"processed": 1, "succeeded": 1, "recovered": recovered}
        finally:
            heartbeat_task.cancel()
            await asyncio.gather(heartbeat_task, return_exceptions=True)

    async def _heartbeat_item(self, action_id: int, lease_lost: asyncio.Event) -> None:
        while True:
            await asyncio.sleep(self.item_heartbeat_seconds)
            try:
                alive = await asyncio.to_thread(
                    self.action_queue.heartbeat,
                    action_id,
                    worker_id=self.worker_id,
                    lease_seconds=self.item_lease_seconds,
                )
            except Exception:
                alive = False
            if not alive:
                lease_lost.set()
                return

    def _lease_lost(self, action: AgentAction) -> dict:
        _log_event(self.event_logger, action, "action_lease_lost", {"worker_id": self.worker_id})
        return {"processed": 1, "lease_lost": 1}


async def run_action_worker(
    action_queue,
    execute: AsyncActionExecutor,
    deliver: AsyncActionResultDelivery,
    *,
    interval_seconds: float = 2,
    stop_after_one_tick: bool = False,
    max_attempts: int = 3,
    event_logger: ActionEventLogger | None = None,
    job_status_updater: JobStatusUpdater | None = None,
) -> None:
    adapter = ActionWorkerAdapter(
        action_queue,
        execute,
        deliver,
        policy=replace(ActionWorkerAdapter.default_policy, interval_seconds=interval_seconds),
        max_attempts=max_attempts,
        event_logger=event_logger,
        job_status_updater=job_status_updater,
    )
    await AutomationRuntime(
        [adapter],
        InMemoryAutomationLeaseStore(),
        poll_seconds=min(1, max(0.01, interval_seconds)),
    ).run(stop_after_one_tick=stop_after_one_tick)


def _success_text(action: AgentAction, result: str) -> str:
    prefix = f"Job #{action.job_id}" if action.job_id is not None else f"Action #{action.id}"
    return f"{prefix}: {result}"


def _failure_text(action: AgentAction, error: Exception) -> str:
    prefix = f"Job #{action.job_id}" if action.job_id is not None else f"Action #{action.id}"
    return f"{prefix}: не выполнил действие. Причина: {error}"


def _coerce_result(result: str | ToolExecutionResult) -> ToolExecutionResult:
    if isinstance(result, ToolExecutionResult):
        return result
    return ToolExecutionResult(str(result))


def _log_event(logger_fn: ActionEventLogger | None, action: AgentAction, event_type: str, meta: dict) -> None:
    if logger_fn is not None:
        logger_fn(action, event_type, meta)


def _block_downstream(action_queue, logger_fn: ActionEventLogger | None, action: AgentAction) -> None:
    if not hasattr(action_queue, "block_dependents"):
        return
    reason = f"Upstream action #{action.id} failed."
    for blocked in action_queue.block_dependents(action.id, reason):
        _log_event(
            logger_fn,
            blocked,
            "action_blocked",
            {"blocked_by_action_id": action.id, "job_id": blocked.job_id, "reason": reason},
        )


def _mark_compensation_skipped(action_queue, logger_fn: ActionEventLogger | None, action: AgentAction) -> None:
    if action.job_id is None or not hasattr(action_queue, "mark_compensation_skipped_for_job"):
        return
    reason = "Rollback tool is not available for this action type."
    for compensated in action_queue.mark_compensation_skipped_for_job(action.job_id, action.id, reason):
        event_type = "compensation_available" if compensated.compensation_status == "available" else "compensation_skipped"
        _log_event(
            logger_fn,
            compensated,
            event_type,
            {
                "failed_action_id": action.id,
                "job_id": action.job_id,
                "reason": reason,
                "result_meta": dict(compensated.result_meta),
            },
        )


def _update_job_status(updater: JobStatusUpdater | None, action: AgentAction) -> None:
    if updater is not None:
        updater(action)
