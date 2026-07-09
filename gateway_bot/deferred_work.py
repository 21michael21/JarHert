from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TypeVar


logger = logging.getLogger(__name__)

T = TypeVar("T")


class DeferredWork:
    def __init__(self, *, fast_ack_seconds: float) -> None:
        if not 0 < fast_ack_seconds < 1:
            raise ValueError("fast_ack_seconds must be greater than zero and lower than one second")
        self.fast_ack_seconds = fast_ack_seconds
        self._tasks: set[asyncio.Task] = set()

    async def submit(
        self,
        work: Awaitable[T],
        *,
        on_result: Callable[[T, bool], None],
        on_ack: Callable[[], None],
        on_error: Callable[[Exception, bool], None],
    ) -> bool:
        task = asyncio.create_task(work)
        self._track(task)
        try:
            result = await asyncio.wait_for(asyncio.shield(task), timeout=self.fast_ack_seconds)
        except asyncio.TimeoutError:
            on_ack()
            self._track(
                asyncio.create_task(
                    self._finish_deferred(task, on_result=on_result, on_error=on_error)
                )
            )
            return True
        except Exception as exc:
            on_error(exc, False)
            return False
        on_result(result, False)
        return False

    async def drain(self) -> None:
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def _finish_deferred(
        self,
        task: asyncio.Task[T],
        *,
        on_result: Callable[[T, bool], None],
        on_error: Callable[[Exception, bool], None],
    ) -> None:
        try:
            on_result(await task, True)
        except Exception as exc:
            on_error(exc, True)

    def _track(self, task: asyncio.Task) -> None:
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
