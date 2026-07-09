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
from assistant.tool_registry import ToolExecutionResult
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
    assert "Нужно одно подтверждение для Job #1" in reply.text
    assert "Подтверди один раз" in reply.text
    assert reply.buttons
    assert reply.buttons[0][0].callback_data == "ai:confirm_job:1"
    assert reply.buttons[0][1].callback_data == "ai:cancel_job:1"
    assert task_center.calls == []
    pending = queue.list_for_user(1)[0]
    assert pending.type == ActionType.TASK_CREATE
    assert pending.status == ActionStatus.NEEDS_CONFIRMATION
    assert queue.claim_next() is None

    confirmed = queue.confirm_job_for_user(1, pending.job_id)
    assert len(confirmed) == 1
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


def test_action_worker_blocks_dependents_and_marks_compensation_on_failure() -> None:
    queue = InMemoryActionQueueStore()
    first = queue.enqueue(
        user_id=1,
        action_type=ActionType.IDEA_SAVE,
        payload={"text": "сделано"},
        job_id=12,
        trace_id="trace-job",
    )
    second = queue.enqueue(
        user_id=1,
        action_type=ActionType.TASK_CREATE,
        payload={"title": "сломать"},
        job_id=12,
        trace_id="trace-job",
        depends_on_action_id=first.id,
    )
    third = queue.enqueue(
        user_id=1,
        action_type=ActionType.MEMORY_SAVE,
        payload={"text": "не запускать"},
        job_id=12,
        trace_id="trace-job",
        depends_on_action_id=second.id,
    )
    queue.mark_succeeded(first.id)
    events = []
    updated_jobs = []

    async def execute(_claimed):
        raise RuntimeError("tool exploded")

    async def deliver(_claimed, _text: str) -> None:
        return None

    def log_event(claimed, event_type, meta) -> None:
        events.append((claimed.id, event_type, meta))

    def update_job(claimed) -> None:
        updated_jobs.append(claimed.job_id)

    asyncio.run(
        run_action_worker(
            queue,
            execute,
            deliver,
            stop_after_one_tick=True,
            event_logger=log_event,
            job_status_updater=update_job,
        )
    )

    items = {item.id: item for item in queue.list_for_user(1, limit=10)}
    assert items[second.id].status == ActionStatus.FAILED
    assert items[third.id].status == ActionStatus.BLOCKED
    assert items[first.id].compensation_status == "not_supported"
    assert "action_blocked" in [event[1] for event in events]
    assert "compensation_skipped" in [event[1] for event in events]
    assert updated_jobs == [12]


def test_action_worker_persists_tool_result_meta() -> None:
    queue = InMemoryActionQueueStore()
    action = queue.enqueue(
        user_id=1,
        action_type=ActionType.TASK_CREATE,
        payload={"title": "сохранить id"},
        job_id=13,
        trace_id="trace-result-meta",
    )
    delivered = []
    events = []

    async def execute(_claimed):
        return ToolExecutionResult("Создал задачу.", meta={"trello_card_id": "card123456"})

    async def deliver(_claimed, text: str) -> None:
        delivered.append(text)

    def log_event(claimed, event_type, meta) -> None:
        events.append((claimed.id, event_type, meta))

    asyncio.run(
        run_action_worker(
            queue,
            execute,
            deliver,
            stop_after_one_tick=True,
            event_logger=log_event,
        )
    )

    saved = next(item for item in queue.list_for_user(1) if item.id == action.id)
    assert saved.result_meta == {"trello_card_id": "card123456"}
    assert "Создал задачу" in delivered[0]
    assert ("action_succeeded", {"trello_card_id": "card123456"}) in [
        (event_type, meta.get("result_meta")) for _, event_type, meta in events
    ]
