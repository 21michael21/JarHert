from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import inspect

from assistant.action_schema import ActionType
from backend.automation_store import SqlAutomationLeaseStore
from backend.db import init_db, make_session_factory
from backend.stores import SqlActionQueueStore, SqlDeliveryOutboxStore, SqlReminderStore, UserStore
from scripts.run_migrations import run_migrations


def session_factory(tmp_path):
    factory = make_session_factory(f"sqlite:///{tmp_path / 'automation.sqlite3'}")
    init_db(factory)
    return factory


def test_sql_lease_claim_is_atomic_and_expired_owner_is_replaced(tmp_path) -> None:
    factory = session_factory(tmp_path)
    first_store = SqlAutomationLeaseStore(factory)
    second_store = SqlAutomationLeaseStore(factory)
    now = datetime(2026, 7, 9, tzinfo=timezone.utc)

    first = first_store.try_acquire("actions", "worker-a", now=now, lease_seconds=30)
    blocked = second_store.try_acquire("actions", "worker-b", now=now, lease_seconds=30)
    recovered = second_store.try_acquire(
        "actions",
        "worker-b",
        now=now + timedelta(seconds=31),
        lease_seconds=30,
    )

    assert first is not None
    assert blocked is None
    assert recovered is not None and recovered.recovered
    assert recovered.generation == first.generation + 1
    assert not first_store.heartbeat(first, now=now + timedelta(seconds=32), lease_seconds=30)
    assert second_store.get("actions").owner_id == "worker-b"


def test_sql_lease_persists_retry_and_success_state(tmp_path) -> None:
    store = SqlAutomationLeaseStore(session_factory(tmp_path))
    now = datetime(2026, 7, 9, tzinfo=timezone.utc)
    claim = store.try_acquire("outbox", "worker", now=now, lease_seconds=30)
    assert claim is not None

    failed = store.fail(
        claim,
        now=now,
        next_run_at=now + timedelta(seconds=5),
        error="network",
        degraded=False,
    )
    assert failed.status == "retry_wait"
    assert failed.failure_count == 1
    assert store.try_acquire("outbox", "worker", now=now + timedelta(seconds=4), lease_seconds=30) is None

    retry = store.try_acquire("outbox", "worker", now=now + timedelta(seconds=5), lease_seconds=30)
    assert retry is not None
    completed = store.complete(
        retry,
        now=now + timedelta(seconds=5),
        next_run_at=now + timedelta(seconds=25),
    )
    assert completed.status == "idle"
    assert completed.failure_count == 0
    assert completed.last_error is None


def test_stale_item_states_are_requeued_for_recovery(tmp_path) -> None:
    factory = session_factory(tmp_path)
    user = UserStore(factory).get_or_create(8001)
    actions = SqlActionQueueStore(factory)
    outbox = SqlDeliveryOutboxStore(factory)
    reminders = SqlReminderStore(factory)

    action = actions.enqueue(user_id=user.id, action_type=ActionType.IDEA_SAVE, payload={"text": "recover"})
    assert actions.claim_next().id == action.id
    message = outbox.enqueue(user_id=user.id, chat_id=user.tg_user_id, text="recover")
    assert outbox.claim_due(limit=1)[0].id == message.id
    reminder = reminders.add(user.id, "recover", datetime.now(timezone.utc) - timedelta(seconds=1))
    assert reminders.claim_due()[0].id == reminder.id

    assert actions.recover_running() == 1
    assert outbox.recover_sending() == 1
    assert reminders.recover_sending() == 1
    assert actions.claim_next().attempts == 2
    assert outbox.claim_due(limit=1)[0].attempts == 2
    assert reminders.claim_due()[0].id == reminder.id


def test_alembic_creates_automation_worker_leases_on_clean_database(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'migrated.sqlite3'}"

    run_migrations(database_url)

    engine = make_session_factory(database_url).kw["bind"]
    inspector = inspect(engine)
    assert "automation_worker_leases" in inspector.get_table_names()
    columns = {column["name"] for column in inspector.get_columns("automation_worker_leases")}
    assert {"worker_name", "owner_id", "generation", "lease_until", "heartbeat_at", "next_run_at"} <= columns
    item_lease_columns = {"worker_id", "lease_until", "claimed_at", "heartbeat_at"}
    assert item_lease_columns <= {column["name"] for column in inspector.get_columns("agent_actions")}
    assert item_lease_columns <= {column["name"] for column in inspector.get_columns("delivery_outbox")}


def test_alembic_creates_update_idempotency_schema(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'idempotency.sqlite3'}"

    run_migrations(database_url)

    engine = make_session_factory(database_url).kw["bind"]
    inspector = inspect(engine)
    assert "inbound_updates" in inspector.get_table_names()
    assert "idempotency_key" in {
        column["name"] for column in inspector.get_columns("agent_jobs")
    }
    assert "idempotency_key" in {
        column["name"] for column in inspector.get_columns("delivery_outbox")
    }
    assert "result_text" in {
        column["name"] for column in inspector.get_columns("agent_actions")
    }
