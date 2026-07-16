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


def test_export_analysis_payload_is_available_to_runner_then_cleared_after_completion(tmp_path) -> None:
    store = NativeCodingJobStore(tmp_path / "personal-os.sqlite3")
    job = store.enqueue(
        tg_user_id=566055009,
        mode="research",
        prompt="Разбери экспорт",
        idempotency_key="telegram:export:job:1",
        source_text="Текст экспорта",
        source_label="chat.txt",
    )

    claimed = store.claim_next(worker_id="mac")
    completed = store.complete(job.id, worker_id="mac", result_text="Готовый разбор")

    assert claimed.source_text == "Текст экспорта"
    assert claimed.source_label == "chat.txt"
    assert completed.source_text is None
    assert completed.source_label == "chat.txt"


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
        "source_label": None,
        "status": "queued",
        "result_summary": None,
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
    assert len(summary["prompt"]) <= 180
    assert len(summary["result_summary"]) <= 160
    assert "result_text" not in summary
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


def test_coding_followups_run_in_order_keep_previous_result_and_only_deliver_the_final_summary(tmp_path) -> None:
    store = NativeCodingJobStore(tmp_path / "personal-os.sqlite3")

    jobs = store.enqueue_chain(
        tg_user_id=566055009,
        mode="coding",
        prompt="Исправь причину и запусти узкие тесты",
        followups=["Проверь diff и подготовь короткий итог для Telegram"],
        repository_url="https://github.com/example/repo",
        idempotency_key="telegram:106:coding-chain",
    )

    assert [job.status for job in jobs] == ["queued", "queued"]
    assert store.claim_next(worker_id="mac").id == jobs[0].id
    assert store.complete(jobs[0].id, worker_id="mac", result_text="Исправлено. 4 теста прошли.").status == "succeeded"

    followup = store.claim_next(worker_id="mac")
    assert followup is not None
    assert followup.id == jobs[1].id
    assert followup.predecessor_result == "Исправлено. 4 теста прошли."
    store.complete(followup.id, worker_id="mac", result_text="Причина найдена, diff готов, 4 теста зелёные.")

    sent: list[tuple[int, str]] = []
    assert dispatch_completed_coding_jobs(store, lambda chat_id, text: sent.append((chat_id, text)) or "telegram:1") == {
        "claimed": 1,
        "sent": 1,
        "failed": 0,
    }
    assert sent == [(566055009, "Причина найдена, diff готов, 4 теста зелёные.")]


def test_replayed_coding_chain_keeps_one_chain_and_one_final_delivery(tmp_path) -> None:
    store = NativeCodingJobStore(tmp_path / "personal-os.sqlite3")
    payload = {
        "tg_user_id": 566055009,
        "mode": "coding",
        "prompt": "Найди причину",
        "followups": ["Проверь diff", "Напиши короткий итог"],
        "idempotency_key": "telegram:108:replayed-chain",
    }

    first = store.enqueue_chain(**payload)
    replay = store.enqueue_chain(**payload)

    assert [job.id for job in replay] == [job.id for job in first]
    assert [job.deliver_result for job in replay] == [False, False, True]
    assert len(store.list_for_user(566055009)) == 3


def test_failed_coding_step_cancels_followups_and_delivers_one_failure(tmp_path) -> None:
    store = NativeCodingJobStore(tmp_path / "personal-os.sqlite3")
    jobs = store.enqueue_chain(
        tg_user_id=566055009,
        mode="coding",
        prompt="Запусти проверку",
        followups=["Собери diff"],
        idempotency_key="telegram:107:failed-chain",
    )

    assert store.claim_next(worker_id="mac").id == jobs[0].id
    store.fail(jobs[0].id, worker_id="mac", error="Тесты не прошли")
    assert store.claim_next(worker_id="mac") is None
    assert store.get_for_user(jobs[1].id, tg_user_id=566055009).status == "cancelled"

    sent: list[str] = []
    assert dispatch_completed_coding_jobs(store, lambda _chat_id, text: sent.append(text) or "telegram:1") == {
        "claimed": 1,
        "sent": 1,
        "failed": 0,
    }
    assert sent == [f"Задача #{jobs[0].id} не выполнилась. Попробуй ещё раз."]
