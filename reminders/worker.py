from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import replace

from assistant.automation_runtime import AutomationRuntime, InMemoryAutomationLeaseStore, WorkerPolicy
from backend.stores import SqlReminderStore
from reminders.store import Reminder


logger = logging.getLogger(__name__)

AsyncReminderSender = Callable[[Reminder], Awaitable[None]]


class ReminderWorkerAdapter:
    name = "reminders"
    default_policy = WorkerPolicy(interval_seconds=30, timeout_seconds=45, lease_seconds=75, heartbeat_seconds=15)

    def __init__(self, store, send: AsyncReminderSender, *, policy: WorkerPolicy | None = None) -> None:
        self.store = store
        self.send = send
        self.policy = policy or self.default_policy

    async def recover_stale(self) -> int:
        recover = getattr(self.store, "recover_sending", None)
        return recover() if recover is not None else 0

    async def run_once(self) -> dict:
        due = self.store.claim_due()
        counts = {"processed": len(due), "sent": 0, "retried": 0}
        for reminder in due:
            try:
                await self.send(reminder)
                self.store.mark_sent(reminder.id)
                counts["sent"] += 1
            except Exception:
                logger.exception("Could not send reminder %s", reminder.id)
                self.store.release_for_retry(reminder.id)
                counts["retried"] += 1
        return counts


async def run_reminder_worker(
    store: SqlReminderStore,
    send: AsyncReminderSender,
    *,
    interval_seconds: float = 30,
    stop_after_one_tick: bool = False,
) -> None:
    adapter = ReminderWorkerAdapter(
        store,
        send,
        policy=replace(ReminderWorkerAdapter.default_policy, interval_seconds=interval_seconds),
    )
    await AutomationRuntime(
        [adapter],
        InMemoryAutomationLeaseStore(),
        poll_seconds=min(1, max(0.01, interval_seconds)),
    ).run(stop_after_one_tick=stop_after_one_tick)
