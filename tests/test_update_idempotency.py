from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor

from assistant.action_worker import run_action_worker
from assistant.hermes_client import FakeHermesClient
from assistant.pipeline import AssistantPipeline
from assistant.types import UserContext
from backend.db import init_db, make_session_factory
from backend.stores import (
    EventStore,
    SqlActionQueueStore,
    SqlAgentJobStore,
    SqlDailyLimitStore,
    SqlDeliveryOutboxStore,
    SqlInboundUpdateStore,
    UserStore,
)
from gateway_bot.service import GatewayService


class CountingTaskCenter:
    def __init__(self) -> None:
        self.task_calls = 0
        self.calendar_calls = 0

    def create_task(self, _text: str) -> str:
        self.task_calls += 1
        return "card_id=card-123 https://trello.com/c/card-123/task"

    def create_task_with_calendar(self, **_kwargs) -> str:
        self.task_calls += 1
        self.calendar_calls += 1
        return "card_id=card-123 calendar_event_id=event-123"

    def create_calendar_event(self, _text: str) -> str:
        self.calendar_calls += 1
        return "calendar_event_id=event-123"


def test_inbound_update_claim_is_atomic_for_ten_workers(tmp_path) -> None:
    factory = make_session_factory(f"sqlite:///{tmp_path / 'claim.sqlite3'}")
    init_db(factory)
    user = UserStore(factory).get_or_create(8099)
    store = SqlInboundUpdateStore(factory)

    with ThreadPoolExecutor(max_workers=10) as pool:
        claims = list(
            pool.map(
                lambda _: store.claim(user.id, "telegram:8099:100"),
                range(10),
            )
        )

    assert sum(claim.acquired for claim in claims) == 1


def test_replay_ten_times_creates_one_external_side_effect_and_delivery(tmp_path) -> None:
    factory = make_session_factory(f"sqlite:///{tmp_path / 'replay.sqlite3'}")
    init_db(factory)
    users = UserStore(factory)
    jobs = SqlAgentJobStore(factory)
    actions = SqlActionQueueStore(factory)
    outbox = SqlDeliveryOutboxStore(factory)
    task_center = CountingTaskCenter()
    pipeline = AssistantPipeline(
        FakeHermesClient(),
        SqlDailyLimitStore(factory),
        task_center=task_center,
        agent_jobs=jobs,
        action_queue=actions,
    )
    service = GatewayService(
        pipeline=pipeline,
        users=users,
        events=EventStore(factory),
        inbound_updates=SqlInboundUpdateStore(factory),
    )
    cases = [
        ("telegram:8100:101", "/task Проверить Trello | list=Today"),
        (
            "telegram:8100:102",
            "/calendar Созвон | start=2026-07-10 10:00 | end=2026-07-10 10:30",
        ),
    ]

    for root_key, command in cases:
        replies = [
            service.handle_text(8100, command, idempotency_key=root_key)
            for _ in range(10)
        ]
        db_user = users.get_or_create(8100)
        for reply in replies:
            outbox.enqueue(
                user_id=db_user.id,
                chat_id=db_user.tg_user_id,
                text=reply.text,
                idempotency_key=f"{root_key}:reply",
            )

    db_user = users.get_or_create(8100)
    assert len(jobs.list_for_user(db_user.id, limit=20)) == 2
    queued = actions.list_for_user(db_user.id, limit=20)
    assert len(queued) == 2
    for action in queued:
        service.confirm_job(8100, action.job_id)

    async def execute(action):
        return pipeline.execute_queued_action_result(
            UserContext(user_id=db_user.id, tg_user_id=db_user.tg_user_id),
            action,
        )

    async def deliver(action, text: str) -> None:
        outbox.enqueue(
            user_id=action.user_id,
            chat_id=db_user.tg_user_id,
            text=text,
            idempotency_key=f"{action.idempotency_key}:result",
        )

    for _ in range(10):
        asyncio.run(
            run_action_worker(
                actions,
                execute,
                deliver,
                stop_after_one_tick=True,
            )
        )

    assert task_center.task_calls == 1
    assert task_center.calendar_calls == 1
    completed = actions.list_for_user(db_user.id, limit=20)
    assert all(action.result_text for action in completed)
    assert all(action.result_meta["idempotency_key"] == action.idempotency_key for action in completed)
    assert any(action.result_meta.get("trello_card_id") for action in completed)
    assert any(action.result_meta.get("calendar_event_id") for action in completed)
    assert len(outbox.list_recent(limit=20)) == 4
