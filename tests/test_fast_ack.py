from __future__ import annotations

import asyncio
import time

from assistant.action_queue import ActionStatus, InMemoryActionQueueStore
from assistant.action_schema import ActionType
from assistant.action_worker import run_action_worker
from assistant.delivery_outbox import InMemoryDeliveryOutboxStore
from assistant.hermes_client import FakeHermesClient
from assistant.limits import DailyLimitStore
from assistant.pipeline import AssistantPipeline
from assistant.types import UserContext


class SlowTaskCenter:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def create_task(self, text):
        self.calls.append(text)
        time.sleep(1.2)
        return "created"

    def create_task_with_calendar(self, **kwargs):
        self.calls.append(str(kwargs))
        time.sleep(1.2)
        return "created"


def user() -> UserContext:
    return UserContext(user_id=1, tg_user_id=1001)


def test_heavy_natural_action_returns_fast_ack_and_queues_work() -> None:
    queue = InMemoryActionQueueStore()
    task_center = SlowTaskCenter()
    pipeline = AssistantPipeline(
        FakeHermesClient(),
        DailyLimitStore(),
        plain_text_ai_enabled=True,
        task_center=task_center,
        action_queue=queue,
    )

    started = time.perf_counter()
    reply = pipeline.handle_text(user(), "создай задачу проверить сервер")
    elapsed = time.perf_counter() - started

    assert elapsed < 1
    assert "Принял, выполняю. Job #1." in reply.text
    assert "отдельным сообщением" in reply.text
    assert task_center.calls == []
    queued = queue.claim_next()
    assert queued is not None
    assert queued.type == ActionType.TASK_CREATE
    assert queued.status == ActionStatus.RUNNING


def test_action_worker_executes_queued_action_and_delivers_result() -> None:
    queue = InMemoryActionQueueStore()
    outbox = InMemoryDeliveryOutboxStore()
    action = queue.enqueue(
        user_id=1,
        action_type=ActionType.TASK_CREATE,
        payload={"title": "проверить сервер"},
        job_id=12,
    )

    async def execute(claimed):
        assert claimed.id == action.id
        return "Создал задачу «проверить сервер»."

    async def deliver(claimed, text: str) -> None:
        outbox.enqueue(user_id=claimed.user_id, chat_id=1001, text=text)

    asyncio.run(run_action_worker(queue, execute, deliver, stop_after_one_tick=True))

    assert queue.list_for_user(1)[0].status == ActionStatus.SUCCEEDED
    delivered = outbox.list_recent(limit=1)[0]
    assert delivered.text == "Job #12: Создал задачу «проверить сервер»."
