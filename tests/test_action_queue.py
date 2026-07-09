from __future__ import annotations

from assistant.action_queue import ActionStatus, InMemoryActionQueueStore
from assistant.action_schema import ActionType
from backend.db import init_db, make_session_factory
from backend.stores import SqlActionQueueStore, UserStore


def session_factory(tmp_path):
    factory = make_session_factory(f"sqlite:///{tmp_path / 'test.sqlite3'}")
    init_db(factory)
    return factory


def test_in_memory_queue_deduplicates_by_idempotency_key() -> None:
    queue = InMemoryActionQueueStore()

    first = queue.enqueue(
        user_id=1,
        action_type=ActionType.IDEA_SAVE,
        payload={"text": "одна идея"},
        idempotency_key="u1:idea:one",
    )
    second = queue.enqueue(
        user_id=1,
        action_type=ActionType.IDEA_SAVE,
        payload={"text": "другая идея"},
        idempotency_key="u1:idea:one",
    )

    assert second.id == first.id
    assert queue.list_for_user(1) == [first]


def test_in_memory_queue_can_retry_failed_action() -> None:
    queue = InMemoryActionQueueStore()
    action = queue.enqueue(
        user_id=1,
        action_type=ActionType.REMINDER_CREATE,
        payload={"text": "через 10 минут проверить"},
    )

    claimed = queue.claim_next()
    assert claimed is not None
    assert claimed.status == ActionStatus.RUNNING
    assert claimed.attempts == 1

    failed = queue.mark_failed(claimed.id, "temporary provider error")
    assert failed.status == ActionStatus.FAILED
    assert failed.last_error == "temporary provider error"

    retried = queue.retry_failed(failed.id)
    assert retried.status == ActionStatus.QUEUED
    assert queue.claim_next().attempts == 2


def test_sql_action_queue_persists_and_deduplicates(tmp_path) -> None:
    factory = session_factory(tmp_path)
    user = UserStore(factory).get_or_create(9101)
    queue_one = SqlActionQueueStore(factory)
    queue_two = SqlActionQueueStore(factory)

    created = queue_one.enqueue(
        user_id=user.id,
        action_type=ActionType.MEMORY_SAVE,
        payload={"text": "OAuth обновлять заранее"},
        idempotency_key="memory:oauth",
    )
    duplicate = queue_two.enqueue(
        user_id=user.id,
        action_type=ActionType.MEMORY_SAVE,
        payload={"text": "другая заметка"},
        idempotency_key="memory:oauth",
    )

    assert duplicate.id == created.id
    assert duplicate.payload == {"text": "OAuth обновлять заранее"}
    assert queue_two.list_for_user(user.id)[0].status == ActionStatus.QUEUED


def test_sql_action_queue_claims_and_retries_failed_action(tmp_path) -> None:
    factory = session_factory(tmp_path)
    user = UserStore(factory).get_or_create(9102)
    queue = SqlActionQueueStore(factory)
    action = queue.enqueue(
        user_id=user.id,
        action_type=ActionType.TASK_CREATE,
        payload={"title": "проверить очередь"},
    )

    claimed = queue.claim_next()
    assert claimed.id == action.id
    assert claimed.status == ActionStatus.RUNNING
    assert claimed.attempts == 1

    failed = queue.mark_failed(claimed.id, "task api timeout")
    assert failed.status == ActionStatus.FAILED
    assert failed.last_error == "task api timeout"

    retried = queue.retry_failed(failed.id)
    assert retried.status == ActionStatus.QUEUED
    assert queue.claim_next().attempts == 2


def test_queue_can_hold_confirmation_actions() -> None:
    queue = InMemoryActionQueueStore()

    action = queue.enqueue(
        user_id=1,
        action_type=ActionType.CALENDAR_CREATE,
        payload={"title": "созвон", "start": "tomorrow 10:00", "end": "tomorrow 10:30"},
        status=ActionStatus.NEEDS_CONFIRMATION,
    )

    assert action.status == ActionStatus.NEEDS_CONFIRMATION
    assert queue.claim_next() is None


def test_queue_confirms_action_and_preserves_trace_id() -> None:
    queue = InMemoryActionQueueStore()
    action = queue.enqueue(
        user_id=1,
        action_type=ActionType.TASK_CREATE,
        payload={"title": "проверить approval"},
        status=ActionStatus.NEEDS_CONFIRMATION,
        trace_id="trace-1",
    )

    confirmed = queue.confirm_for_user(1, action.id)

    assert confirmed is not None
    assert confirmed.status == ActionStatus.QUEUED
    assert confirmed.trace_id == "trace-1"
    assert queue.claim_next().trace_id == "trace-1"


def test_sql_queue_confirms_action_and_preserves_trace_id(tmp_path) -> None:
    factory = session_factory(tmp_path)
    user = UserStore(factory).get_or_create(9103)
    queue = SqlActionQueueStore(factory)
    action = queue.enqueue(
        user_id=user.id,
        action_type=ActionType.CALENDAR_CREATE,
        payload={"title": "созвон", "start": "tomorrow 10:00", "end": "tomorrow 10:30"},
        status=ActionStatus.NEEDS_CONFIRMATION,
        trace_id="trace-sql",
    )

    confirmed = queue.confirm_for_user(user.id, action.id)

    assert confirmed is not None
    assert confirmed.status == ActionStatus.QUEUED
    assert confirmed.trace_id == "trace-sql"
    assert queue.claim_next().trace_id == "trace-sql"
