from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from backend.stores import SqlReminderStore
from reminders.store import Reminder


logger = logging.getLogger(__name__)

AsyncReminderSender = Callable[[Reminder], Awaitable[None]]


async def run_reminder_worker(
    store: SqlReminderStore,
    send: AsyncReminderSender,
    *,
    interval_seconds: float = 30,
    stop_after_one_tick: bool = False,
) -> None:
    while True:
        due = store.claim_due()
        for reminder in due:
            try:
                await send(reminder)
                store.mark_sent(reminder.id)
            except Exception:
                logger.exception("Could not send reminder %s", reminder.id)
                store.release_for_retry(reminder.id)
        if stop_after_one_tick:
            return
        await asyncio.sleep(interval_seconds)
