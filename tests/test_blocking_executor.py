from __future__ import annotations

import asyncio
import threading
import time

import pytest

from gateway_bot.blocking_executor import BlockingCallBusy, BlockingCallTimeout, BoundedUserExecutor


def test_global_concurrency_is_bounded_without_serializing_other_users() -> None:
    executor = BoundedUserExecutor(max_concurrency=2, timeout_seconds=2)
    started: list[str] = []
    all_started = threading.Event()
    release = threading.Event()

    def work(label: str) -> str:
        started.append(label)
        if len(started) == 2:
            all_started.set()
        release.wait(timeout=1)
        return label

    async def scenario() -> list[str]:
        tasks = [
            asyncio.create_task(executor.run_blocking(user_id, work, label))
            for user_id, label in ((1, "one"), (2, "two"), (3, "three"))
        ]
        assert await asyncio.to_thread(all_started.wait, 0.5)
        assert len(started) == 2
        release.set()
        return await asyncio.gather(*tasks)

    try:
        assert asyncio.run(scenario()) == ["one", "two", "three"]
    finally:
        executor.close()


def test_same_user_work_keeps_submission_order() -> None:
    executor = BoundedUserExecutor(max_concurrency=2, timeout_seconds=2)
    order: list[str] = []
    first_started = threading.Event()
    release_first = threading.Event()

    def first() -> str:
        order.append("first-start")
        first_started.set()
        release_first.wait(timeout=1)
        order.append("first-end")
        return "first"

    def second() -> str:
        order.append("second")
        return "second"

    async def scenario() -> tuple[str, str]:
        first_task = asyncio.create_task(executor.run_blocking(77, first))
        assert await asyncio.to_thread(first_started.wait, 0.5)
        second_task = asyncio.create_task(executor.run_blocking(77, second))
        await asyncio.sleep(0.05)
        assert order == ["first-start"]
        release_first.set()
        return await asyncio.gather(first_task, second_task)

    try:
        assert asyncio.run(scenario()) == ["first", "second"]
        assert order == ["first-start", "first-end", "second"]
    finally:
        executor.close()


def test_timeout_is_reported_to_caller() -> None:
    executor = BoundedUserExecutor(max_concurrency=1, timeout_seconds=0.02)

    try:
        with pytest.raises(BlockingCallTimeout):
            asyncio.run(executor.run_blocking(1, time.sleep, 0.1))
    finally:
        executor.close()


def test_same_user_gets_fast_busy_error_after_a_timed_out_call() -> None:
    executor = BoundedUserExecutor(
        max_concurrency=1,
        timeout_seconds=0.02,
        late_result_grace_seconds=0.01,
    )
    first_started = threading.Event()
    release_first = threading.Event()
    order: list[str] = []

    def first() -> None:
        order.append("first-start")
        first_started.set()
        release_first.wait(timeout=1)
        order.append("first-end")

    def second() -> str:
        order.append("second")
        return "second"

    async def scenario() -> None:
        with pytest.raises(BlockingCallTimeout):
            await executor.run_blocking(5, first)
        assert await asyncio.to_thread(first_started.wait, 0.5)
        with pytest.raises(BlockingCallBusy):
            await asyncio.wait_for(executor.run_blocking(5, second), timeout=0.1)
        assert order == ["first-start"]
        release_first.set()
        await asyncio.sleep(0.03)
        assert await executor.run_blocking(5, second) == "second"

    try:
        asyncio.run(scenario())
        assert order == ["first-start", "first-end", "second"]
    finally:
        executor.close()
