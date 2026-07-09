from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from assistant.hermes_client import FakeHermesClient
from assistant.pipeline import AssistantPipeline
from assistant.types import UserContext
from backend.db import init_db, make_session_factory
from backend.models import Event
from backend.stores import (
    EventStore,
    SqlAgentJobStore,
    SqlDailyLimitStore,
    SqlDeliveryOutboxStore,
    SqlIdeaStore,
    SqlMemoryStore,
    SqlMonitorJobStore,
    SqlReminderStore,
    UserStore,
)
from gateway_bot.service import GatewayService
from reminders.worker import run_reminder_worker


def session_factory(tmp_path):
    factory = make_session_factory(f"sqlite:///{tmp_path / 'test.sqlite3'}")
    init_db(factory)
    return factory


def test_sql_memory_persists_between_service_instances(tmp_path) -> None:
    factory = session_factory(tmp_path)
    users = UserStore(factory)
    service_one = GatewayService(
        pipeline=AssistantPipeline(
            FakeHermesClient(),
            SqlDailyLimitStore(factory),
            memories=SqlMemoryStore(factory),
            ideas=SqlIdeaStore(factory),
            reminders=SqlReminderStore(factory),
        ),
        users=users,
        events=EventStore(factory),
    )
    service_two = GatewayService(
        pipeline=AssistantPipeline(
            FakeHermesClient(),
            SqlDailyLimitStore(factory),
            memories=SqlMemoryStore(factory),
            ideas=SqlIdeaStore(factory),
            reminders=SqlReminderStore(factory),
        ),
        users=users,
        events=EventStore(factory),
    )

    assert "Сохранил" in service_one.handle_text(7001, "/remember постоянная память").text
    assert "постоянная память" in service_two.handle_text(7001, "/memories").text


def test_sql_ideas_persist_between_service_instances(tmp_path) -> None:
    factory = session_factory(tmp_path)
    users = UserStore(factory)
    service_one = GatewayService(
        pipeline=AssistantPipeline(
            FakeHermesClient(),
            SqlDailyLimitStore(factory),
            ideas=SqlIdeaStore(factory),
            memories=SqlMemoryStore(factory),
            reminders=SqlReminderStore(factory),
        ),
        users=users,
        events=EventStore(factory),
    )
    service_two = GatewayService(
        pipeline=AssistantPipeline(
            FakeHermesClient(),
            SqlDailyLimitStore(factory),
            ideas=SqlIdeaStore(factory),
            memories=SqlMemoryStore(factory),
            reminders=SqlReminderStore(factory),
        ),
        users=users,
        events=EventStore(factory),
    )

    assert "Сохранил идею" in service_one.handle_text(7010, "/idea постоянная идея").text
    assert "постоянная идея" in service_two.handle_text(7010, "/ideas").text


def test_sql_daily_limit_persists(tmp_path) -> None:
    factory = session_factory(tmp_path)
    user = UserStore(factory).get_or_create(7002)
    limits_one = SqlDailyLimitStore(factory, per_user_limit=1, global_limit=10)
    limits_two = SqlDailyLimitStore(factory, per_user_limit=1, global_limit=10)

    assert limits_one.consume(user.id)
    assert not limits_two.consume(user.id)


def test_gateway_logs_events(tmp_path) -> None:
    factory = session_factory(tmp_path)
    service = GatewayService(
        pipeline=AssistantPipeline(
            FakeHermesClient(),
            SqlDailyLimitStore(factory),
            memories=SqlMemoryStore(factory),
            reminders=SqlReminderStore(factory),
        ),
        users=UserStore(factory),
        events=EventStore(factory),
    )
    service.handle_text(7003, "/ask привет")

    with factory() as db:
        events = db.query(Event).all()
    assert len(events) == 1
    assert events[0].type == "assistant_ask"
    assert events[0].meta["provider"] == "fake"
    assert events[0].meta["perf_ms"]["total_response_ms"] >= 0


def test_reminder_worker_claims_due_once(tmp_path) -> None:
    factory = session_factory(tmp_path)
    user = UserStore(factory).get_or_create(7004)
    store = SqlReminderStore(factory)
    store.add(user.id, "проверить worker", datetime.now(timezone.utc) - timedelta(seconds=1))
    sent = []

    async def send(reminder) -> None:
        sent.append(reminder.text)

    asyncio.run(run_reminder_worker(store, send, stop_after_one_tick=True))
    asyncio.run(run_reminder_worker(store, send, stop_after_one_tick=True))

    assert sent == ["проверить worker"]


def test_reminder_worker_retries_if_send_fails(tmp_path) -> None:
    factory = session_factory(tmp_path)
    user = UserStore(factory).get_or_create(7006)
    store = SqlReminderStore(factory)
    store.add(user.id, "retry worker", datetime.now(timezone.utc) - timedelta(seconds=1))
    attempts = []

    async def send(reminder) -> None:
        attempts.append(reminder.text)
        if len(attempts) == 1:
            raise RuntimeError("telegram failed")

    asyncio.run(run_reminder_worker(store, send, stop_after_one_tick=True))
    asyncio.run(run_reminder_worker(store, send, stop_after_one_tick=True))
    asyncio.run(run_reminder_worker(store, send, stop_after_one_tick=True))

    assert attempts == ["retry worker", "retry worker"]


def test_sql_cancel_reminder(tmp_path) -> None:
    factory = session_factory(tmp_path)
    user = UserStore(factory).get_or_create(7005)
    store = SqlReminderStore(factory)
    reminder = store.add(user.id, "отменить", datetime.now(timezone.utc) + timedelta(hours=1))

    assert store.cancel_for_user(user.id, reminder.id)
    assert store.list_pending_for_user(user.id) == []


def test_sql_stores_use_internal_user_id_not_tg_id(tmp_path) -> None:
    factory = session_factory(tmp_path)
    db_user = UserStore(factory).get_or_create(999_000_111)
    memory = SqlMemoryStore(factory)
    memory.add(db_user.id, "internal id ok")

    assert memory.list_for_user(db_user.id)[0].text == "internal id ok"
    assert memory.list_for_user(999_000_111) == []


def test_sql_delivery_outbox_persists_retry_and_permanent_failure(tmp_path) -> None:
    factory = session_factory(tmp_path)
    user = UserStore(factory).get_or_create(7007)
    store = SqlDeliveryOutboxStore(factory)
    message = store.enqueue(user_id=user.id, chat_id=user.tg_user_id, text="ответ")

    claimed = store.claim_due(limit=1)
    assert claimed[0].id == message.id
    assert claimed[0].attempts == 1

    retry_at = datetime.now(timezone.utc) + timedelta(minutes=1)
    store.mark_retry(message.id, "timeout", retry_at)
    assert store.stats()["queued"] == 1
    assert store.claim_due(now=datetime.now(timezone.utc)) == []

    claimed_again = store.claim_due(now=retry_at + timedelta(seconds=1), limit=1)
    assert claimed_again[0].attempts == 2

    store.mark_failed_permanent(message.id, "chat not found")
    assert store.stats()["failed"] == 1


def test_sql_agent_jobs_persist_and_are_user_scoped(tmp_path) -> None:
    factory = session_factory(tmp_path)
    users = UserStore(factory)
    user_one = users.get_or_create(7101)
    user_two = users.get_or_create(7102)
    store_one = SqlAgentJobStore(factory)
    store_two = SqlAgentJobStore(factory)

    created = store_one.create(user_one.id, "проверить календарь", ["проверить доступ", "показать итог"])

    assert created.id == 1
    assert store_two.get_for_user(user_one.id, created.id).goal == "проверить календарь"
    assert store_two.get_for_user(user_two.id, created.id) is None
    assert store_two.list_for_user(user_two.id) == []


def test_sql_monitor_jobs_persist_and_record_runs(tmp_path) -> None:
    factory = session_factory(tmp_path)
    users = UserStore(factory)
    user_one = users.get_or_create(7201)
    user_two = users.get_or_create(7202)
    store = SqlMonitorJobStore(factory)

    created = store.create(
        user_id=user_one.id,
        chat_id=user_one.tg_user_id,
        source_type="github_releases",
        source_config={"owner": "owner", "repo": "repo"},
        condition_text="напиши если новый релиз",
    )
    store.mark_checked(created.id, state_hash="abc", payload={"tag": "v1"})
    run = store.record_run(created.id, status="not_triggered", triggered=False)

    own = store.list_enabled()
    assert own[0].source_config == {"owner": "owner", "repo": "repo"}
    assert own[0].chat_id == user_one.tg_user_id
    assert own[0].last_state_hash == "abc"
    assert own[0].last_payload == {"tag": "v1"}
    assert run.status == "not_triggered"
    assert user_two.id != own[0].user_id


def test_sql_monitor_jobs_are_user_scoped_when_disabled(tmp_path) -> None:
    factory = session_factory(tmp_path)
    users = UserStore(factory)
    user_one = users.get_or_create(7211)
    user_two = users.get_or_create(7212)
    store = SqlMonitorJobStore(factory)
    created = store.create(
        user_id=user_one.id,
        chat_id=user_one.tg_user_id,
        source_type="github_releases",
        source_config={"owner": "openai", "repo": "codex"},
        condition_text="напиши если важный релиз",
    )

    assert store.disable_for_user(user_two.id, created.id) is False
    assert store.get(created.id).enabled is True
    assert store.disable_for_user(user_one.id, created.id) is True

    disabled = store.get(created.id)
    assert disabled.enabled is False
    assert store.list_for_user(user_one.id)[0].id == created.id
    assert store.list_for_user(user_two.id) == []
