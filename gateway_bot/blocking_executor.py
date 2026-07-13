from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import ParamSpec, TypeVar


logger = logging.getLogger(__name__)

P = ParamSpec("P")
T = TypeVar("T")


class BlockingCallTimeout(TimeoutError):
    pass


class BlockingCallBusy(RuntimeError):
    pass


class BoundedUserExecutor:
    """Run blocking work off the event loop with global and per-user limits."""

    def __init__(
        self,
        *,
        max_concurrency: int,
        timeout_seconds: float,
        late_result_grace_seconds: float = 1.0,
    ) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be positive")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if late_result_grace_seconds <= 0:
            raise ValueError("late_result_grace_seconds must be positive")
        self.max_concurrency = max_concurrency
        self.timeout_seconds = timeout_seconds
        self.late_result_grace_seconds = late_result_grace_seconds
        self._executor = ThreadPoolExecutor(
            max_workers=max_concurrency,
            thread_name_prefix="jarhert-blocking",
        )
        self._loop: asyncio.AbstractEventLoop | None = None
        self._semaphore: asyncio.Semaphore | None = None
        self._user_locks: dict[int, asyncio.Lock] = {}
        self._timed_out: dict[int, asyncio.Future[object]] = {}
        self._closed = False

    async def run_blocking(
        self,
        user_id: int,
        func: Callable[P, T],
        /,
        *args: P.args,
        timeout_seconds: float | None = None,
        **kwargs: P.kwargs,
    ) -> T:
        return await self.run_serialized(
            user_id,
            lambda: self.run_blocking_unlocked(
                user_id,
                func,
                *args,
                timeout_seconds=timeout_seconds,
                **kwargs,
            ),
        )

    async def run_serialized(
        self,
        user_id: int,
        operation: Callable[[], Awaitable[T]],
    ) -> T:
        self._ensure_loop()
        await self._wait_for_timed_out_user(user_id)
        lock = self._user_locks.setdefault(user_id, asyncio.Lock())
        async with lock:
            await self._wait_for_timed_out_user(user_id)
            return await operation()

    async def run_blocking_unlocked(
        self,
        user_id: int,
        func: Callable[P, T],
        /,
        *args: P.args,
        timeout_seconds: float | None = None,
        **kwargs: P.kwargs,
    ) -> T:
        self._ensure_loop()
        await self._wait_for_timed_out_user(user_id)
        assert self._semaphore is not None
        timeout = timeout_seconds if timeout_seconds is not None else self.timeout_seconds
        if timeout <= 0:
            raise ValueError("timeout_seconds must be positive")

        async with self._semaphore:
            loop = asyncio.get_running_loop()
            future = loop.run_in_executor(self._executor, partial(func, *args, **kwargs))
            try:
                return await asyncio.wait_for(asyncio.shield(future), timeout=timeout)
            except asyncio.TimeoutError as exc:
                self._timed_out[user_id] = future
                future.add_done_callback(_consume_late_result)
                raise BlockingCallTimeout(f"blocking call exceeded {timeout:.1f}s") from exc

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _ensure_loop(self) -> None:
        if self._closed:
            raise RuntimeError("blocking executor is closed")
        loop = asyncio.get_running_loop()
        if self._loop is None:
            self._loop = loop
            self._semaphore = asyncio.Semaphore(self.max_concurrency)
            return
        if self._loop is not loop:
            raise RuntimeError("BoundedUserExecutor must be used from one event loop")

    async def _wait_for_timed_out_user(self, user_id: int) -> None:
        pending = self._timed_out.get(user_id)
        if pending is None:
            return
        if pending.done():
            self._timed_out.pop(user_id, None)
            return
        try:
            await asyncio.wait_for(asyncio.shield(pending), timeout=self.late_result_grace_seconds)
        except asyncio.TimeoutError as exc:
            raise BlockingCallBusy("previous blocking call is still running") from exc
        except Exception:
            pass
        finally:
            if self._timed_out.get(user_id) is pending and pending.done():
                self._timed_out.pop(user_id, None)


def _consume_late_result(future: asyncio.Future[object]) -> None:
    try:
        future.result()
    except Exception:
        logger.warning("Timed-out blocking call finished with an error", exc_info=True)


_shared_executor: BoundedUserExecutor | None = None


def get_shared_executor(*, max_concurrency: int, timeout_seconds: float) -> BoundedUserExecutor:
    global _shared_executor
    if _shared_executor is None:
        _shared_executor = BoundedUserExecutor(
            max_concurrency=max_concurrency,
            timeout_seconds=timeout_seconds,
        )
    return _shared_executor


def close_shared_executor() -> None:
    global _shared_executor
    if _shared_executor is not None:
        _shared_executor.close()
        _shared_executor = None
