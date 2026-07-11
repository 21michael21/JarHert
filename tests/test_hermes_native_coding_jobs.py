from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from hermes.native_tools.coding_jobs import NativeCodingJobStore
from hermes.native_tools.mcp_api import NativeToolsAPI


def test_native_coding_queue_is_idempotent_and_uses_one_lease(tmp_path) -> None:
    store = NativeCodingJobStore(tmp_path / "personal-os.sqlite3")
    first = store.enqueue(
        tg_user_id=566055009,
        mode="coding",
        prompt="Добавь тест",
        repository_url="https://github.com/example/repo",
        source_urls=[],
        idempotency_key="telegram:100:coding",
    )
    replay = store.enqueue(
        tg_user_id=566055009,
        mode="coding",
        prompt="дубль",
        repository_url=None,
        source_urls=[],
        idempotency_key="telegram:100:coding",
    )
    claimed = store.claim_next(worker_id="mac-main", now=datetime(2030, 1, 1, tzinfo=timezone.utc))

    assert replay == first
    assert claimed.id == first.id
    assert store.claim_next(worker_id="second") is None
    assert store.heartbeat(first.id, worker_id="mac-main") is True
    with pytest.raises(PermissionError):
        store.complete(first.id, worker_id="second", result_text="чужой результат")
    assert store.complete(first.id, worker_id="mac-main", result_text="Готово: 3 теста прошли").status == "succeeded"


def test_expired_native_coding_job_can_be_claimed_after_mac_stops(tmp_path) -> None:
    store = NativeCodingJobStore(tmp_path / "personal-os.sqlite3")
    job = store.enqueue(
        tg_user_id=566055009,
        mode="research",
        prompt="Проверь документацию",
        source_urls=["https://docs.python.org/3/"],
        idempotency_key="telegram:101:research",
    )
    started = datetime(2030, 1, 1, tzinfo=timezone.utc)
    store.claim_next(worker_id="dead-mac", now=started, lease_seconds=1)

    recovered = store.claim_next(
        worker_id="replacement-mac",
        now=started + timedelta(seconds=2),
    )

    assert recovered.id == job.id
    assert recovered.worker_id == "replacement-mac"


def test_native_api_queues_code_only_in_code_mode_and_lists_results(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HERMES_OWNER_TELEGRAM_CHAT_ID", "566055009")
    api = NativeToolsAPI(database_path=tmp_path / "personal-os.sqlite3")

    with pytest.raises(PermissionError):
        api.coding_job_enqueue(mode="coding", prompt="Добавь тест", idempotency_key="telegram:102:coding")

    api.work_mode_set(mode="code")
    queued = api.coding_job_enqueue(mode="coding", prompt="Добавь тест", idempotency_key="telegram:102:coding")
    assert queued["status"] == "queued"
    assert api.coding_job_list()["items"] == [queued]
