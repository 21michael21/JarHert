from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from assistant.automation_runtime import (
    AutomationRuntime,
    InMemoryAutomationLeaseStore,
    WorkerPolicy,
)


@dataclass
class FakeAdapter:
    name: str = "fake"
    policy: WorkerPolicy = field(default_factory=lambda: WorkerPolicy(interval_seconds=0))
    failures_left: int = 0
    delay_seconds: float = 0
    runs: int = 0
    recoveries: int = 0

    async def recover_stale(self) -> int:
        self.recoveries += 1
        return 1

    async def run_once(self) -> dict:
        self.runs += 1
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        if self.failures_left:
            self.failures_left -= 1
            raise RuntimeError("temporary failure")
        return {"processed": 1}


class Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 7, 9, tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += timedelta(seconds=seconds)


def test_worker_lease_has_single_atomic_owner() -> None:
    clock = Clock()
    store = InMemoryAutomationLeaseStore()

    first = store.try_acquire("actions", "worker-a", now=clock.now(), lease_seconds=30)
    second = store.try_acquire("actions", "worker-b", now=clock.now(), lease_seconds=30)

    assert first is not None
    assert second is None
    assert store.get("actions").owner_id == "worker-a"


def test_expired_lease_is_recovered_before_adapter_runs() -> None:
    clock = Clock()
    store = InMemoryAutomationLeaseStore()
    assert store.try_acquire("actions", "dead-worker", now=clock.now(), lease_seconds=1)
    clock.advance(2)
    adapter = FakeAdapter(name="actions")
    events = []
    runtime = AutomationRuntime(
        [adapter],
        store,
        owner_id="replacement",
        now=clock.now,
        event_logger=lambda event, meta: events.append((event, meta)),
    )

    asyncio.run(runtime.tick())

    assert adapter.recoveries == 1
    assert adapter.runs == 1
    assert any(event == "worker_recovered" for event, _ in events)


def test_runtime_timeout_uses_backoff_and_retry_budget() -> None:
    clock = Clock()
    store = InMemoryAutomationLeaseStore()
    adapter = FakeAdapter(
        name="slow",
        policy=WorkerPolicy(
            interval_seconds=0,
            timeout_seconds=0.01,
            retry_budget=2,
            backoff_base_seconds=5,
        ),
        delay_seconds=0.05,
    )
    runtime = AutomationRuntime([adapter], store, owner_id="worker", now=clock.now)

    asyncio.run(runtime.tick())
    first = store.get("slow")
    assert first.status == "retry_wait"
    assert first.failure_count == 1
    assert first.next_run_at == clock.now() + timedelta(seconds=5)

    clock.advance(5)
    asyncio.run(runtime.tick())
    second = store.get("slow")
    assert second.status == "degraded"
    assert second.failure_count == 2
    assert second.next_run_at == clock.now() + timedelta(seconds=10)


def test_heartbeat_is_emitted_while_adapter_is_running() -> None:
    store = InMemoryAutomationLeaseStore()
    adapter = FakeAdapter(
        name="heartbeat",
        policy=WorkerPolicy(
            interval_seconds=0,
            timeout_seconds=1,
            lease_seconds=0.05,
            heartbeat_seconds=0.005,
        ),
        delay_seconds=0.025,
    )
    events = []
    runtime = AutomationRuntime(
        [adapter],
        store,
        owner_id="worker",
        event_logger=lambda event, meta: events.append((event, meta)),
    )

    asyncio.run(runtime.tick())

    assert adapter.runs == 1
    assert any(event == "worker_heartbeat" for event, _ in events)
    assert store.get("heartbeat").status == "idle"


def test_success_resets_failure_budget_and_schedules_next_tick() -> None:
    clock = Clock()
    store = InMemoryAutomationLeaseStore()
    adapter = FakeAdapter(
        name="retry",
        policy=WorkerPolicy(interval_seconds=20, retry_budget=3, backoff_base_seconds=2),
        failures_left=1,
    )
    runtime = AutomationRuntime([adapter], store, owner_id="worker", now=clock.now)

    asyncio.run(runtime.tick())
    assert store.get("retry").failure_count == 1
    clock.advance(2)
    asyncio.run(runtime.tick())

    lease = store.get("retry")
    assert lease.status == "idle"
    assert lease.failure_count == 0
    assert lease.next_run_at == clock.now() + timedelta(seconds=20)


def test_lost_lease_does_not_stop_runtime_tick() -> None:
    class LostOnCompleteStore(InMemoryAutomationLeaseStore):
        def complete(self, claim, *, now, next_run_at):
            raise RuntimeError("automation lease lost")

    events = []
    runtime = AutomationRuntime(
        [FakeAdapter(name="lost")],
        LostOnCompleteStore(),
        event_logger=lambda event, meta: events.append((event, meta)),
    )

    asyncio.run(runtime.tick())

    assert any(event == "worker_lease_lost" for event, _ in events)


def test_claim_store_failure_isolated_from_other_adapters() -> None:
    class FailingOnceStore(InMemoryAutomationLeaseStore):
        def __init__(self):
            super().__init__()
            self.failed = False

        def try_acquire(self, worker_name, owner_id, *, now, lease_seconds):
            if worker_name == "broken" and not self.failed:
                self.failed = True
                raise RuntimeError("database unavailable")
            return super().try_acquire(worker_name, owner_id, now=now, lease_seconds=lease_seconds)

    broken = FakeAdapter(name="broken")
    healthy = FakeAdapter(name="healthy")
    events = []
    runtime = AutomationRuntime(
        [broken, healthy],
        FailingOnceStore(),
        event_logger=lambda event, meta: events.append((event, meta)),
    )

    asyncio.run(runtime.tick())

    assert broken.runs == 0
    assert healthy.runs == 1
    assert any(event == "worker_claim_failed" for event, _ in events)
