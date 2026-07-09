from __future__ import annotations

from assistant.delivery_outbox import InMemoryDeliveryOutboxStore
from assistant.hermes_client import FakeHermesClient
from assistant.monitors.runner import hash_payload, run_monitors_once
from assistant.types import HermesResponse
from backend.db import init_db, make_session_factory
from backend.stores import SqlMonitorJobStore, UserStore


def session_factory(tmp_path):
    factory = make_session_factory(f"sqlite:///{tmp_path / 'test.sqlite3'}")
    init_db(factory)
    return factory


def create_job(tmp_path):
    factory = session_factory(tmp_path)
    user = UserStore(factory).get_or_create(9001)
    store = SqlMonitorJobStore(factory)
    job = store.create(
        user_id=user.id,
        chat_id=user.tg_user_id,
        source_type="github_releases",
        source_config={"owner": "openai", "repo": "codex"},
        condition_text="напиши только если релиз важный",
    )
    return store, job


def test_monitor_no_change_does_not_call_llm_or_send(tmp_path) -> None:
    store, job = create_job(tmp_path)
    payload = {"tag_name": "v1", "name": "Release 1"}
    store.mark_checked(job.id, state_hash=hash_payload(payload), payload=payload)
    hermes = FakeHermesClient([HermesResponse(text='{"triggered": true, "message": "не должно вызваться"}')])
    outbox = InMemoryDeliveryOutboxStore()

    summary = run_monitors_once(monitor_jobs=store, hermes=hermes, delivery_outbox=outbox, fetcher=lambda _: payload)

    assert summary["no_change"] == 1
    assert hermes.requests == []
    assert outbox.stats()["queued"] == 0
    assert store.list_runs(job.id)[0].status == "no_change"


def test_monitor_changed_not_triggered_stays_silent(tmp_path) -> None:
    store, job = create_job(tmp_path)
    payload = {"tag_name": "v2", "name": "Release 2"}
    hermes = FakeHermesClient([HermesResponse(text='{"triggered": false, "message": null}')])
    outbox = InMemoryDeliveryOutboxStore()

    summary = run_monitors_once(monitor_jobs=store, hermes=hermes, delivery_outbox=outbox, fetcher=lambda _: payload)

    assert summary["not_triggered"] == 1
    assert len(hermes.requests) == 1
    assert outbox.stats()["queued"] == 0
    assert store.get(job.id).last_state_hash == hash_payload(payload)
    assert store.list_runs(job.id)[0].status == "not_triggered"


def test_monitor_changed_triggered_enqueues_delivery(tmp_path) -> None:
    store, job = create_job(tmp_path)
    payload = {"tag_name": "v3", "name": "Release 3", "html_url": "https://example.test/release"}
    hermes = FakeHermesClient([HermesResponse(text='{"triggered": true, "message": "Вышел важный релиз v3"}')])
    outbox = InMemoryDeliveryOutboxStore()

    summary = run_monitors_once(monitor_jobs=store, hermes=hermes, delivery_outbox=outbox, fetcher=lambda _: payload)

    assert summary["triggered"] == 1
    assert outbox.stats()["queued"] == 1
    delivery = outbox.claim_due(limit=1)[0]
    assert delivery.text == "Вышел важный релиз v3"
    assert delivery.chat_id == 9001
    assert store.list_runs(job.id)[0].status == "triggered"
