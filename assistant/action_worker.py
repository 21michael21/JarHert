from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from assistant.action_queue import AgentAction
from assistant.tool_registry import ToolExecutionError


logger = logging.getLogger(__name__)


AsyncActionExecutor = Callable[[AgentAction], Awaitable[str]]
AsyncActionResultDelivery = Callable[[AgentAction, str], Awaitable[None]]
ActionEventLogger = Callable[[AgentAction, str, dict], None]


async def run_action_worker(
    action_queue,
    execute: AsyncActionExecutor,
    deliver: AsyncActionResultDelivery,
    *,
    interval_seconds: float = 2,
    stop_after_one_tick: bool = False,
    max_attempts: int = 3,
    event_logger: ActionEventLogger | None = None,
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
            result = await execute(action)
        except Exception as error:
            retryable = isinstance(error, ToolExecutionError) and error.retryable
            if retryable and action.attempts < max_attempts:
                action_queue.retry_failed(action.id)
                logger.info("action worker retry queued: action_id=%s attempts=%s", action.id, action.attempts)
                _log_event(event_logger, action, "tool_failed", {"retryable": True, "error": str(error)})
            else:
                action_queue.mark_failed(action.id, str(error) or error.__class__.__name__)
                _log_event(event_logger, action, "tool_failed", {"retryable": False, "error": str(error)})
                await deliver(action, _failure_text(action, error))
            if stop_after_one_tick:
                return
            await asyncio.sleep(interval_seconds)
            continue

        action_queue.mark_succeeded(action.id)
        _log_event(event_logger, action, "action_succeeded", {"result_chars": len(result)})
        await deliver(action, _success_text(action, result))
        if stop_after_one_tick:
            return
        await asyncio.sleep(interval_seconds)


def _success_text(action: AgentAction, result: str) -> str:
    prefix = f"Job #{action.job_id}" if action.job_id is not None else f"Action #{action.id}"
    return f"{prefix}: {result}"


def _failure_text(action: AgentAction, error: Exception) -> str:
    prefix = f"Job #{action.job_id}" if action.job_id is not None else f"Action #{action.id}"
    return f"{prefix}: не выполнил действие. Причина: {error}"


def _log_event(logger_fn: ActionEventLogger | None, action: AgentAction, event_type: str, meta: dict) -> None:
    if logger_fn is not None:
        logger_fn(action, event_type, meta)
