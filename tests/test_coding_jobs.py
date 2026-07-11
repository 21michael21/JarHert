from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from assistant.automation_runtime import LeaseLostError
from backend.coding_job_store import SqlCodingJobStore
from backend.db import init_db, make_session_factory
from backend.stores import UserStore


def _store(tmp_path):
    factory = make_session_factory(f"sqlite:///{tmp_path / 'coding.sqlite3'}")
    init_db(factory)
    user = UserStore(factory).get_or_create(1001)
    return SqlCodingJobStore(factory), user.id


def test_coding_job_is_idempotent_and_claimed_by_one_remote_runner(tmp_path) -> None:
    store, user_id = _store(tmp_path)
    first = store.enqueue(
        user_id=user_id,
        mode="coding",
        prompt="Исправь баг и добавь тест",
        repository_url="https://github.com/example/project",
        idempotency_key="telegram:1001:55:coding",
    )
    replay = store.enqueue(
        user_id=user_id,
        mode="coding",
        prompt="Исправь баг и добавь тест",
        repository_url="https://github.com/example/project",
        idempotency_key="telegram:1001:55:coding",
    )

    claimed = store.claim_next(worker_id="mac-a", lease_seconds=30)

    assert replay.id == first.id
    assert claimed.id == first.id
    assert store.claim_next(worker_id="mac-b", lease_seconds=30) is None
    with pytest.raises(LeaseLostError):
        store.complete(first.id, worker_id="mac-b", result_text="чужой результат")
    completed = store.complete(first.id, worker_id="mac-a", result_text="tests passed")
    assert completed.status == "succeeded"
    assert completed.result_text == "tests passed"


def test_expired_coding_job_returns_to_remote_queue(tmp_path) -> None:
    store, user_id = _store(tmp_path)
    job = store.enqueue(
        user_id=user_id,
        mode="research",
        prompt="Проверь документацию",
        source_urls=["https://docs.python.org/3/"],
    )
    started = datetime(2030, 1, 1, tzinfo=timezone.utc)
    store.claim_next(worker_id="dead-mac", now=started, lease_seconds=1)

    recovered = store.claim_next(
        worker_id="replacement-mac",
        now=started + timedelta(seconds=2),
        lease_seconds=30,
    )

    assert recovered.id == job.id
    assert recovered.worker_id == "replacement-mac"
