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
    assert "Нужно подтверждение для Job #1" in reply.text
    assert "Без подтверждения" in reply.text
    assert reply.buttons
    assert task_center.calls == []
    pending = queue.list_for_user(1)[0]
    assert pending.type == ActionType.TASK_CREATE
    assert pending.status == ActionStatus.NEEDS_CONFIRMATION
    assert queue.claim_next() is None

    confirmed = queue.confirm_for_user(1, pending.id)
    assert confirmed is not None
    queued = queue.claim_next()
    assert queued is not None
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


def test_action_worker_emits_lifecycle_events() -> None:
    queue = InMemoryActionQueueStore()
    action = queue.enqueue(
        user_id=1,
        action_type=ActionType.TASK_CREATE,
        payload={"title": "проверить trace"},
        job_id=12,
        trace_id="trace-action",
    )
    events = []

    async def execute(claimed):
        assert claimed.id == action.id
        return "ok"

    async def deliver(_claimed, _text: str) -> None:
        return None

    def log_event(claimed, event_type, meta) -> None:
        events.append((claimed.trace_id, event_type, meta))

    asyncio.run(run_action_worker(queue, execute, deliver, stop_after_one_tick=True, event_logger=log_event))

    assert [event[1] for event in events] == ["action_started", "action_succeeded"]
    assert all(event[0] == "trace-action" for event in events)
