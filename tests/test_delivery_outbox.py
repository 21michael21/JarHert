from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from assistant.delivery_outbox import (
    DeliveryStatus,
    InMemoryDeliveryOutboxStore,
    classify_delivery_error,
    run_delivery_outbox_worker,
)


class RetryAfterError(Exception):
    retry_after = 42


def test_delivery_outbox_claims_and_marks_sent() -> None:
    store = InMemoryDeliveryOutboxStore()
    queued = store.enqueue(
        user_id=1,
        chat_id=1001,
        text="готово",
        trace_id="trace-delivery",
        buttons=[[{"text": "Статус", "callback_data": "ai:status:1"}]],
    )

    due = store.claim_due(now=datetime.now(timezone.utc))
    assert len(due) == 1
    assert due[0].id == queued.id
    assert due[0].status == DeliveryStatus.SENDING
    assert due[0].attempts == 1
    assert due[0].trace_id == "trace-delivery"
    assert due[0].buttons[0][0]["callback_data"] == "ai:status:1"

    sent = store.mark_sent(queued.id)
    assert sent.status == DeliveryStatus.SENT
    assert store.stats()["sent"] == 1


def test_delivery_outbox_retries_timeout_and_429() -> None:
    timeout = classify_delivery_error(TimeoutError("timed out"))
    rate_limited = classify_delivery_error(RetryAfterError("Too Many Requests: retry after 42"))

    assert timeout.retryable
    assert timeout.retry_after_seconds is None
    assert rate_limited.retryable
    assert rate_limited.retry_after_seconds == 42


def test_delivery_outbox_marks_chat_not_found_permanent() -> None:
    result = classify_delivery_error(RuntimeError("Bad Request: chat not found"))

    assert not result.retryable


def test_delivery_worker_retries_retryable_error_without_raising() -> None:
    store = InMemoryDeliveryOutboxStore()
    store.enqueue(user_id=1, chat_id=1001, text="пинг")

    async def send(_message) -> None:
        raise TimeoutError("telegram timeout")

    asyncio.run(run_delivery_outbox_worker(store, send, stop_after_one_tick=True))

    recent = store.list_recent(limit=1)[0]
    assert recent.status == DeliveryStatus.QUEUED
    assert recent.attempts == 1
    assert recent.last_error == "telegram timeout"
    assert recent.next_attempt_at is not None
    assert recent.next_attempt_at > datetime.now(timezone.utc)


def test_delivery_worker_marks_permanent_error_failed() -> None:
    store = InMemoryDeliveryOutboxStore()
    store.enqueue(user_id=1, chat_id=1001, text="пинг")

    async def send(_message) -> None:
        raise RuntimeError("chat not found")

    asyncio.run(run_delivery_outbox_worker(store, send, stop_after_one_tick=True))

    recent = store.list_recent(limit=1)[0]
    assert recent.status == DeliveryStatus.FAILED
    assert recent.attempts == 1
    assert recent.next_attempt_at is None


def test_delivery_worker_emits_lifecycle_events() -> None:
    store = InMemoryDeliveryOutboxStore()
    store.enqueue(user_id=1, chat_id=1001, text="пинг", trace_id="trace-delivery")
    events = []

    async def send(_message) -> None:
        return None

    def log_event(message, event_type, meta) -> None:
        events.append((message.trace_id, event_type, meta))

    asyncio.run(run_delivery_outbox_worker(store, send, stop_after_one_tick=True, event_logger=log_event))

    assert len(events) == 1
    trace_id, event_type, meta = events[0]
    assert trace_id == "trace-delivery"
    assert event_type == "delivery_sent"
    assert meta["attempts"] == 1
    assert isinstance(meta["queue_lag_ms"], int)
    assert isinstance(meta["delivery_latency_ms"], int)


def test_delivery_outbox_skips_not_due_messages() -> None:
    store = InMemoryDeliveryOutboxStore()
    future = datetime.now(timezone.utc) + timedelta(minutes=5)
    store.enqueue(user_id=1, chat_id=1001, text="позже", next_attempt_at=future)

    assert store.claim_due(now=datetime.now(timezone.utc)) == []


def test_replayed_update_is_delivered_once() -> None:
    store = InMemoryDeliveryOutboxStore()
    for _ in range(10):
        store.enqueue(
            user_id=1,
            chat_id=1001,
            text="один ответ",
            idempotency_key="telegram:1001:4242:reply",
        )
    sent = []

    async def send(message) -> None:
        sent.append(message.text)

    for _ in range(10):
        asyncio.run(run_delivery_outbox_worker(store, send, stop_after_one_tick=True))

    assert sent == ["один ответ"]
