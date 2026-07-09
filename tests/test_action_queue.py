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


def test_in_memory_queue_respects_dependencies_and_blocks_downstream() -> None:
    queue = InMemoryActionQueueStore()
    first = queue.enqueue(
        user_id=1,
        action_type=ActionType.IDEA_SAVE,
        payload={"text": "подготовить"},
        job_id=10,
    )
    second = queue.enqueue(
        user_id=1,
        action_type=ActionType.MEMORY_SAVE,
        payload={"text": "зависит от первого"},
        job_id=10,
        depends_on_action_id=first.id,
    )

    claimed = queue.claim_next()
    assert claimed.id == first.id
    assert queue.claim_next() is None

    queue.mark_failed(first.id, "primary failed")
    blocked = queue.block_dependents(first.id, "Upstream action failed.")

    assert [item.id for item in blocked] == [second.id]
    second_after = next(item for item in queue.list_for_user(1) if item.id == second.id)
    assert second_after.status == ActionStatus.BLOCKED
    assert "Upstream" in second_after.last_error


def test_in_memory_queue_unblocks_dependent_after_retry_success() -> None:
    queue = InMemoryActionQueueStore()
    first = queue.enqueue(
        user_id=1,
        action_type=ActionType.IDEA_SAVE,
        payload={"text": "upstream"},
        job_id=10,
    )
    second = queue.enqueue(
        user_id=1,
        action_type=ActionType.MEMORY_SAVE,
        payload={"text": "downstream"},
        job_id=10,
        depends_on_action_id=first.id,
    )
    queue.mark_failed(first.id, "temporary")
    queue.block_dependents(first.id, "Upstream action failed.")

    queue.retry_failed(first.id)
    claimed = queue.claim_next()
    assert claimed.id == first.id
    queue.mark_succeeded(first.id)

    second_after = next(item for item in queue.list_for_user(1) if item.id == second.id)
    assert second_after.status == ActionStatus.QUEUED
    assert second_after.last_error is None
    assert queue.claim_next().id == second.id


def test_in_memory_queue_marks_compensation_skipped_for_successful_previous_actions() -> None:
    queue = InMemoryActionQueueStore()
    first = queue.enqueue(
        user_id=1,
        action_type=ActionType.IDEA_SAVE,
        payload={"text": "уже сделано"},
        job_id=10,
    )
    failed = queue.enqueue(
        user_id=1,
        action_type=ActionType.TASK_CREATE,
        payload={"title": "сломалось"},
        job_id=10,
        depends_on_action_id=first.id,
    )
    queue.mark_succeeded(first.id)

    compensated = queue.mark_compensation_skipped_for_job(10, failed.id, "no rollback")

    assert [item.id for item in compensated] == [first.id]
    first_after = next(item for item in queue.list_for_user(1) if item.id == first.id)
    assert first_after.compensation_status == "not_supported"
    assert first_after.last_error == "no rollback"


def test_in_memory_queue_persists_result_meta_and_marks_compensation_available() -> None:
    queue = InMemoryActionQueueStore()
    first = queue.enqueue(
        user_id=1,
        action_type=ActionType.TASK_CREATE,
        payload={"title": "уже сделано"},
        job_id=10,
    )
    failed = queue.enqueue(
        user_id=1,
        action_type=ActionType.CALENDAR_CREATE,
        payload={"title": "сломалось", "start": "2026-07-10 10:00", "end": "2026-07-10 10:30"},
        job_id=10,
        depends_on_action_id=first.id,
    )
    queue.mark_succeeded(first.id, result_meta={"trello_card_id": "card123456"})

    compensated = queue.mark_compensation_skipped_for_job(10, failed.id, "no rollback")

    assert [item.id for item in compensated] == [first.id]
    first_after = next(item for item in queue.list_for_user(1) if item.id == first.id)
    assert first_after.result_meta == {"trello_card_id": "card123456"}
    assert first_after.compensation_status == "available"


def test_sql_action_queue_respects_dependencies_and_compensation(tmp_path) -> None:
    factory = session_factory(tmp_path)
    user = UserStore(factory).get_or_create(9104)
    queue = SqlActionQueueStore(factory)
    first = queue.enqueue(
        user_id=user.id,
        action_type=ActionType.IDEA_SAVE,
        payload={"text": "подготовить"},
        job_id=20,
    )
    second = queue.enqueue(
        user_id=user.id,
        action_type=ActionType.MEMORY_SAVE,
        payload={"text": "после первого"},
        job_id=20,
        depends_on_action_id=first.id,
    )

    assert queue.claim_next().id == first.id
    assert queue.claim_next() is None
    queue.mark_succeeded(first.id)
    assert queue.claim_next().id == second.id
    queue.mark_failed(second.id, "tool failed")

    compensated = queue.mark_compensation_skipped_for_job(20, second.id, "manual rollback required")

    assert [item.id for item in compensated] == [first.id]
    first_after = next(item for item in queue.list_for_user(user.id) if item.id == first.id)
    assert first_after.depends_on_action_id is None
    assert first_after.compensation_status == "not_supported"


def test_sql_queue_persists_result_meta_and_marks_compensation_available(tmp_path) -> None:
    factory = session_factory(tmp_path)
    user = UserStore(factory).get_or_create(9106)
    queue = SqlActionQueueStore(factory)
    first = queue.enqueue(
        user_id=user.id,
        action_type=ActionType.TASK_CREATE,
        payload={"title": "создать"},
        job_id=21,
    )
    failed = queue.enqueue(
        user_id=user.id,
        action_type=ActionType.CALENDAR_CREATE,
        payload={"title": "сломать", "start": "2026-07-10 10:00", "end": "2026-07-10 10:30"},
        job_id=21,
        depends_on_action_id=first.id,
    )
    queue.mark_succeeded(first.id, result_meta={"trello_card_id": "abc"})

    compensated = queue.mark_compensation_skipped_for_job(21, failed.id, "manual rollback required")

    assert [item.id for item in compensated] == [first.id]
    first_after = next(item for item in queue.list_for_user(user.id) if item.id == first.id)
    assert first_after.result_meta == {"trello_card_id": "abc"}
    assert first_after.compensation_status == "available"


def test_sql_queue_marks_compensation_available_for_result_url(tmp_path) -> None:
    factory = session_factory(tmp_path)
    user = UserStore(factory).get_or_create(9107)
    queue = SqlActionQueueStore(factory)
    first = queue.enqueue(
        user_id=user.id,
        action_type=ActionType.TASK_CREATE,
        payload={"title": "создать"},
        job_id=22,
    )
    failed = queue.enqueue(
        user_id=user.id,
        action_type=ActionType.CALENDAR_CREATE,
        payload={"title": "сломать", "start": "2026-07-10 10:00", "end": "2026-07-10 10:30"},
        job_id=22,
        depends_on_action_id=first.id,
    )
    queue.mark_succeeded(first.id, result_meta={"trello_card_url": "https://trello.example/card/abc"})

    compensated = queue.mark_compensation_skipped_for_job(22, failed.id, "manual rollback required")

    assert [item.id for item in compensated] == [first.id]
    first_after = next(item for item in queue.list_for_user(user.id) if item.id == first.id)
    assert first_after.result_meta == {"trello_card_url": "https://trello.example/card/abc"}
    assert first_after.compensation_status == "available"


def test_sql_action_queue_unblocks_dependent_after_retry_success(tmp_path) -> None:
    factory = session_factory(tmp_path)
    user = UserStore(factory).get_or_create(9105)
    queue = SqlActionQueueStore(factory)
    first = queue.enqueue(
        user_id=user.id,
        action_type=ActionType.IDEA_SAVE,
        payload={"text": "upstream"},
        job_id=30,
    )
    second = queue.enqueue(
        user_id=user.id,
        action_type=ActionType.MEMORY_SAVE,
        payload={"text": "downstream"},
        job_id=30,
        depends_on_action_id=first.id,
    )
    queue.mark_failed(first.id, "temporary")
    queue.block_dependents(first.id, "Upstream action failed.")

    queue.retry_failed(first.id)
    assert queue.claim_next().id == first.id
    queue.mark_succeeded(first.id)

    second_after = next(item for item in queue.list_for_user(user.id) if item.id == second.id)
    assert second_after.status == ActionStatus.QUEUED
    assert second_after.last_error is None
    assert queue.claim_next().id == second.id


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


def test_queue_confirms_whole_job_once() -> None:
    queue = InMemoryActionQueueStore()
    first = queue.enqueue(
        user_id=1,
        action_type=ActionType.TASK_CREATE,
        payload={"title": "первая"},
        job_id=7,
        status=ActionStatus.NEEDS_CONFIRMATION,
    )
    second = queue.enqueue(
        user_id=1,
        action_type=ActionType.CALENDAR_CREATE,
        payload={"title": "вторая", "start": "tomorrow 10:00", "end": "tomorrow 10:30"},
        job_id=7,
        status=ActionStatus.NEEDS_CONFIRMATION,
        depends_on_action_id=first.id,
    )

    confirmed = queue.confirm_job_for_user(1, 7)

    assert [item.id for item in confirmed] == [first.id, second.id]
    assert queue.claim_next().id == first.id


def test_queue_cancels_whole_job_once() -> None:
    queue = InMemoryActionQueueStore()
    first = queue.enqueue(
        user_id=1,
        action_type=ActionType.TASK_CREATE,
        payload={"title": "первая"},
        job_id=7,
        status=ActionStatus.NEEDS_CONFIRMATION,
    )
    second = queue.enqueue(
        user_id=1,
        action_type=ActionType.CALENDAR_CREATE,
        payload={"title": "вторая", "start": "tomorrow 10:00", "end": "tomorrow 10:30"},
        job_id=7,
        status=ActionStatus.NEEDS_CONFIRMATION,
    )

    cancelled = queue.cancel_job_for_user(1, 7)

    assert [item.id for item in cancelled] == [first.id, second.id]
    assert queue.claim_next() is None
    assert {item.status for item in queue.list_for_user(1)} == {ActionStatus.CANCELLED}


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


def test_sql_queue_confirms_and_cancels_whole_job(tmp_path) -> None:
    factory = session_factory(tmp_path)
    user = UserStore(factory).get_or_create(9106)
    queue = SqlActionQueueStore(factory)
    first = queue.enqueue(
        user_id=user.id,
        action_type=ActionType.TASK_CREATE,
        payload={"title": "первая"},
        job_id=77,
        status=ActionStatus.NEEDS_CONFIRMATION,
    )
    second = queue.enqueue(
        user_id=user.id,
        action_type=ActionType.CALENDAR_CREATE,
        payload={"title": "вторая", "start": "tomorrow 10:00", "end": "tomorrow 10:30"},
        job_id=77,
        status=ActionStatus.NEEDS_CONFIRMATION,
        depends_on_action_id=first.id,
    )

    confirmed = queue.confirm_job_for_user(user.id, 77)

    assert [item.id for item in confirmed] == [first.id, second.id]
    assert queue.claim_next().id == first.id

    third = queue.enqueue(
        user_id=user.id,
        action_type=ActionType.TASK_CREATE,
        payload={"title": "третья"},
        job_id=78,
        status=ActionStatus.NEEDS_CONFIRMATION,
    )
    cancelled = queue.cancel_job_for_user(user.id, 78)

    assert [item.id for item in cancelled] == [third.id]
    assert next(item for item in queue.list_for_user(user.id) if item.id == third.id).status == ActionStatus.CANCELLED
