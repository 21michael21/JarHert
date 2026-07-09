from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier

import pytest

from assistant.action_schema import ActionType
from assistant.action_worker import ActionWorkerAdapter
from assistant.delivery_outbox import DeliveryOutboxAdapter
from backend.db import init_db, make_session_factory
from backend.queue_store import LeaseLostError
from backend.stores import SqlActionQueueStore, SqlDeliveryOutboxStore, UserStore


def session_factory(tmp_path):
    factory = make_session_factory(f"sqlite:///{tmp_path / 'item-leases.sqlite3'}")
    init_db(factory)
    return factory


def test_two_workers_cannot_claim_same_action(tmp_path) -> None:
    factory = session_factory(tmp_path)
    user = UserStore(factory).get_or_create(9101)
    queued = SqlActionQueueStore(factory).enqueue(
        user_id=user.id,
        action_type=ActionType.TASK_CREATE,
        payload={"title": "single execution"},
    )
    barrier = Barrier(2)
    now = datetime.now(timezone.utc)

    def claim(worker_id: str):
        barrier.wait()
        return SqlActionQueueStore(factory).claim_next(worker_id=worker_id, now=now, lease_seconds=30)

    with ThreadPoolExecutor(max_workers=2) as pool:
        claimed = list(pool.map(claim, ["worker-a", "worker-b"]))

    winners = [item for item in claimed if item is not None]
    assert [item.id for item in winners] == [queued.id]
    assert winners[0].worker_id in {"worker-a", "worker-b"}
    assert winners[0].claimed_at == now
    assert winners[0].heartbeat_at == now
    assert winners[0].lease_until == now + timedelta(seconds=30)


def test_two_workers_cannot_claim_same_delivery(tmp_path) -> None:
    factory = session_factory(tmp_path)
    user = UserStore(factory).get_or_create(9102)
    queued = SqlDeliveryOutboxStore(factory).enqueue(user_id=user.id, chat_id=user.tg_user_id, text="once")
    barrier = Barrier(2)
    now = datetime.now(timezone.utc)

    def claim(worker_id: str):
        barrier.wait()
        return SqlDeliveryOutboxStore(factory).claim_due(
            worker_id=worker_id,
            now=now,
            lease_seconds=30,
            limit=1,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        claimed_batches = list(pool.map(claim, ["worker-a", "worker-b"]))

    winners = [item for batch in claimed_batches for item in batch]
    assert [item.id for item in winners] == [queued.id]
    assert winners[0].worker_id in {"worker-a", "worker-b"}
    assert winners[0].lease_until == now + timedelta(seconds=30)


def test_killed_action_worker_loses_fencing_after_expired_recovery(tmp_path) -> None:
    factory = session_factory(tmp_path)
    user = UserStore(factory).get_or_create(9103)
    store = SqlActionQueueStore(factory)
    queued = store.enqueue(user_id=user.id, action_type=ActionType.TASK_CREATE, payload={"title": "recover"})
    started = datetime.now(timezone.utc)
    first = store.claim_next(worker_id="dead-worker", now=started, lease_seconds=1)
    assert first is not None

    recovered_at = started + timedelta(seconds=2)
    assert store.recover_expired(now=recovered_at) == 1
    second = store.claim_next(worker_id="replacement", now=recovered_at, lease_seconds=30)
    assert second is not None and second.id == queued.id

    with pytest.raises(LeaseLostError):
        store.mark_succeeded(queued.id, worker_id="dead-worker")
    completed = store.mark_succeeded(queued.id, worker_id="replacement")

    assert completed.attempts == 2
    assert completed.worker_id == "replacement"


def test_killed_delivery_worker_cannot_mark_reclaimed_message_sent(tmp_path) -> None:
    factory = session_factory(tmp_path)
    user = UserStore(factory).get_or_create(9104)
    store = SqlDeliveryOutboxStore(factory)
    queued = store.enqueue(user_id=user.id, chat_id=user.tg_user_id, text="recover")
    started = datetime.now(timezone.utc)
    assert store.claim_due(worker_id="dead-worker", now=started, lease_seconds=1, limit=1)

    recovered_at = started + timedelta(seconds=2)
    assert store.recover_expired(now=recovered_at) == 1
    second = store.claim_due(worker_id="replacement", now=recovered_at, lease_seconds=30, limit=1)
    assert second[0].id == queued.id

    with pytest.raises(LeaseLostError):
        store.mark_sent(queued.id, worker_id="dead-worker")
    completed = store.mark_sent(queued.id, worker_id="replacement")

    assert completed.attempts == 2
    assert completed.worker_id == "replacement"


def test_action_heartbeat_prevents_reclaim_during_long_tool_call(tmp_path) -> None:
    factory = session_factory(tmp_path)
    user = UserStore(factory).get_or_create(9105)
    store = SqlActionQueueStore(factory)
    store.enqueue(user_id=user.id, action_type=ActionType.TASK_CREATE, payload={"title": "slow"})
    started = asyncio.Event()
    release = asyncio.Event()
    executions = []

    async def execute(action):
        executions.append(action.id)
        started.set()
        await release.wait()
        return "done"

    async def deliver(_action, _text):
        return None

    first = ActionWorkerAdapter(
        store,
        execute,
        deliver,
        worker_id="worker-a",
        item_lease_seconds=0.05,
        item_heartbeat_seconds=0.01,
    )

    async def scenario():
        task = asyncio.create_task(first.run_once())
        await started.wait()
        await asyncio.sleep(0.08)
        assert store.recover_expired() == 0
        assert store.claim_next(worker_id="worker-b", lease_seconds=1) is None
        release.set()
        await task

    asyncio.run(scenario())
    assert executions == [1]


def test_delivery_heartbeat_prevents_second_sender(tmp_path) -> None:
    factory = session_factory(tmp_path)
    user = UserStore(factory).get_or_create(9106)
    store = SqlDeliveryOutboxStore(factory)
    store.enqueue(user_id=user.id, chat_id=user.tg_user_id, text="slow")
    started = asyncio.Event()
    release = asyncio.Event()
    sends = []

    async def send(message):
        sends.append(message.id)
        started.set()
        await release.wait()

    first = DeliveryOutboxAdapter(
        store,
        send,
        worker_id="worker-a",
        item_lease_seconds=0.05,
        item_heartbeat_seconds=0.01,
    )

    async def scenario():
        task = asyncio.create_task(first.run_once())
        await started.wait()
        await asyncio.sleep(0.08)
        assert store.recover_expired() == 0
        assert store.claim_due(worker_id="worker-b", lease_seconds=1, limit=1) == []
        release.set()
        await task

    asyncio.run(scenario())
    assert sends == [1]
