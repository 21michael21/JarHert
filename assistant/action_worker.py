from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from assistant.action_queue import AgentAction
from assistant.tool_registry import ToolExecutionError, ToolExecutionResult


logger = logging.getLogger(__name__)


AsyncActionExecutor = Callable[[AgentAction], Awaitable[str | ToolExecutionResult]]
AsyncActionResultDelivery = Callable[[AgentAction, str], Awaitable[None]]
ActionEventLogger = Callable[[AgentAction, str, dict], None]
JobStatusUpdater = Callable[[AgentAction], None]


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
    while True:
        action = action_queue.claim_next()
        if action is None:
            if stop_after_one_tick:
                return
            await asyncio.sleep(interval_seconds)
            continue

        try:
            _log_event(event_logger, action, "action_started", {"attempts": action.attempts})
            raw_result = await execute(action)
            result = _coerce_result(raw_result)
        except Exception as error:
            retryable = isinstance(error, ToolExecutionError) and error.retryable
            if retryable and action.attempts < max_attempts:
                action_queue.retry_failed(action.id)
                logger.info("action worker retry queued: action_id=%s attempts=%s", action.id, action.attempts)
                _log_event(event_logger, action, "tool_failed", {"retryable": True, "error": str(error)})
            else:
                action_queue.mark_failed(action.id, str(error) or error.__class__.__name__)
                _log_event(event_logger, action, "tool_failed", {"retryable": False, "error": str(error)})
                _block_downstream(action_queue, event_logger, action)
                _mark_compensation_skipped(action_queue, event_logger, action)
                _update_job_status(job_status_updater, action)
                await deliver(action, _failure_text(action, error))
            if stop_after_one_tick:
                return
            await asyncio.sleep(interval_seconds)
            continue

        action_queue.mark_succeeded(action.id, result_meta=result.meta)
        _log_event(
            event_logger,
            action,
            "action_succeeded",
            {"result_chars": len(result.message), "result_meta": dict(result.meta)},
        )
        _update_job_status(job_status_updater, action)
        await deliver(action, _success_text(action, result.message))
        if stop_after_one_tick:
            return
        await asyncio.sleep(interval_seconds)


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
