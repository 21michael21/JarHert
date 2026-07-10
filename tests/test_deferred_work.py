from __future__ import annotations

import asyncio
import time

from gateway_bot.deferred_work import DeferredWork


def test_slow_work_acknowledges_before_one_second_and_delivers_final_result() -> None:
    dispatcher = DeferredWork(fast_ack_seconds=0.03)
    events: list[tuple[str, str]] = []

    async def slow_work() -> str:
        await asyncio.sleep(0.08)
        return "готово"

    async def scenario() -> bool:
        started = time.perf_counter()
        deferred = await dispatcher.submit(
            slow_work(),
            on_result=lambda result, delayed: events.append(("result", f"{result}:{delayed}")),
            on_ack=lambda: events.append(("ack", "accepted")),
            on_error=lambda error, delayed: events.append(("error", f"{type(error).__name__}:{delayed}")),
        )
        assert time.perf_counter() - started < 0.5
        await dispatcher.drain()
        return deferred

    assert asyncio.run(scenario())
    assert events == [("ack", "accepted"), ("result", "готово:True")]


def test_slow_work_can_run_without_user_visible_ack() -> None:
    dispatcher = DeferredWork(fast_ack_seconds=0.03)
    events: list[tuple[str, str]] = []

    async def slow_work() -> str:
        await asyncio.sleep(0.08)
        return "готово"

    async def scenario() -> bool:
        deferred = await dispatcher.submit(
            slow_work(),
            on_result=lambda result, delayed: events.append(("result", f"{result}:{delayed}")),
            on_ack=lambda: None,
            on_error=lambda error, delayed: events.append(("error", f"{type(error).__name__}:{delayed}")),
        )
        await dispatcher.drain()
        return deferred

    assert asyncio.run(scenario())
    assert events == [("result", "готово:True")]
