from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from hermes.native_tools.coding_jobs import NativeCodingJobStore, dispatch_completed_coding_jobs
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


def test_native_api_queues_previewed_coding_work_from_fast_mode_and_lists_results(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HERMES_OWNER_TELEGRAM_CHAT_ID", "566055009")
    api = NativeToolsAPI(database_path=tmp_path / "personal-os.sqlite3")

    assert api.coding_job_list() == {"items": []}
    queued = api.coding_job_enqueue(mode="coding", prompt="Добавь тест", idempotency_key="telegram:102:coding")
    assert queued["status"] == "queued"
    summary = api.coding_job_list()["items"]
    assert summary == [{
        "id": queued["id"],
        "mode": "coding",
        "prompt": "Добавь тест",
        "repository_url": None,
        "status": "queued",
        "result_text": None,
        "last_error": None,
        "delivery_status": "pending",
        "created_at": queued["created_at"],
        "updated_at": queued["updated_at"],
    }]


def test_native_api_keeps_coding_list_compact_but_can_return_one_full_report(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HERMES_OWNER_TELEGRAM_CHAT_ID", "566055009")
    api = NativeToolsAPI(database_path=tmp_path / "personal-os.sqlite3")
    queued = api.coding_job_enqueue(
        mode="coding",
        prompt="Проверь " + "важную деталь " * 200,
        idempotency_key="telegram:105:coding",
    )
    store = NativeCodingJobStore(tmp_path / "personal-os.sqlite3")
    store.claim_next(worker_id="mac")
    store.complete(queued["id"], worker_id="mac", result_text="Полный отчёт: " + "diff " * 2_000)

    summary = api.coding_job_list()["items"][0]

    assert summary["id"] == queued["id"]
    assert summary["status"] == "succeeded"
    assert len(summary["prompt"]) <= 320
    assert len(summary["result_text"]) <= 480
    assert "idempotency_key" not in summary
    assert api.coding_job_get(job_id=queued["id"])["result_text"].startswith("Полный отчёт")


def test_completed_native_job_is_claimed_once_for_telegram_delivery(tmp_path) -> None:
    store = NativeCodingJobStore(tmp_path / "personal-os.sqlite3")
    job = store.enqueue(tg_user_id=566055009, mode="research", prompt="Итог", idempotency_key="telegram:103")
    store.claim_next(worker_id="mac")
    store.complete(job.id, worker_id="mac", result_text="Короткий итог")

    delivery = store.claim_completed_for_delivery(worker_id="dispatcher")

    assert delivery.id == job.id
    assert store.claim_completed_for_delivery(worker_id="another") is None
    assert store.mark_delivery_sent(job.id, worker_id="dispatcher").delivery_status == "delivered"


def test_completed_native_job_is_delivered_once_with_a_short_result(tmp_path) -> None:
    store = NativeCodingJobStore(tmp_path / "personal-os.sqlite3")
    job = store.enqueue(tg_user_id=566055009, mode="coding", prompt="Задача", idempotency_key="telegram:104")
    store.claim_next(worker_id="mac")
    store.complete(job.id, worker_id="mac", result_text="Готово: тесты прошли")
    sent: list[tuple[int, str]] = []

    result = dispatch_completed_coding_jobs(store, lambda chat_id, text: sent.append((chat_id, text)) or "telegram:1")
    replay = dispatch_completed_coding_jobs(store, lambda chat_id, text: sent.append((chat_id, text)) or "telegram:2")

    assert result == {"claimed": 1, "sent": 1, "failed": 0}
    assert replay == {"claimed": 0, "sent": 0, "failed": 0}
    assert sent == [(566055009, "Готово: тесты прошли")]
