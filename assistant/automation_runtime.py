from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol


logger = logging.getLogger(__name__)


class LeaseLostError(RuntimeError):
    pass


@dataclass(frozen=True)
class WorkerPolicy:
    interval_seconds: float = 2
    timeout_seconds: float = 60
    lease_seconds: float = 90
    heartbeat_seconds: float = 15
    retry_budget: int = 3
    backoff_base_seconds: float = 5
    backoff_max_seconds: float = 300

    def __post_init__(self) -> None:
        if self.interval_seconds < 0:
            raise ValueError("interval_seconds must be non-negative")
        if self.timeout_seconds <= 0 or self.lease_seconds <= 0 or self.heartbeat_seconds <= 0:
            raise ValueError("timeout, lease and heartbeat must be positive")
        if self.heartbeat_seconds >= self.lease_seconds:
            raise ValueError("heartbeat_seconds must be lower than lease_seconds")
        if self.retry_budget < 1:
            raise ValueError("retry_budget must be positive")


@dataclass(frozen=True)
class WorkerLease:
    worker_name: str
    status: str = "idle"
    owner_id: str | None = None
    generation: int = 0
    failure_count: int = 0
    lease_until: datetime | None = None
    heartbeat_at: datetime | None = None
    next_run_at: datetime | None = None
    last_started_at: datetime | None = None
    last_completed_at: datetime | None = None
    last_error: str | None = None


@dataclass(frozen=True)
class LeaseClaim:
    worker_name: str
    owner_id: str
    generation: int
    recovered: bool = False


class AutomationAdapter(Protocol):
    name: str
    policy: WorkerPolicy

    async def recover_stale(self) -> int:
        """Return the number of item records recovered after stale lease takeover."""

    async def run_once(self) -> dict[str, Any] | None:
        """Process one bounded tick without owning scheduling or retries."""


class AutomationLeaseStore(Protocol):
    def try_acquire(
        self,
        worker_name: str,
        owner_id: str,
        *,
        now: datetime,
        lease_seconds: float,
    ) -> LeaseClaim | None:
        ...

    def heartbeat(self, claim: LeaseClaim, *, now: datetime, lease_seconds: float) -> bool:
        ...

    def complete(self, claim: LeaseClaim, *, now: datetime, next_run_at: datetime) -> WorkerLease:
        ...

    def fail(
        self,
        claim: LeaseClaim,
        *,
        now: datetime,
        next_run_at: datetime,
        error: str,
        degraded: bool,
    ) -> WorkerLease:
        ...

    def get(self, worker_name: str) -> WorkerLease | None:
        ...


class InMemoryAutomationLeaseStore:
    def __init__(self) -> None:
        self._items: dict[str, WorkerLease] = {}

    def try_acquire(
        self,
        worker_name: str,
        owner_id: str,
        *,
        now: datetime,
        lease_seconds: float,
    ) -> LeaseClaim | None:
        current = self._items.get(worker_name) or WorkerLease(worker_name=worker_name)
        if current.next_run_at is not None and current.next_run_at > now:
            return None
        expired = current.lease_until is not None and current.lease_until <= now
        if current.owner_id is not None and current.owner_id != owner_id and not expired:
            return None
        recovered = (
            (current.generation == 0 and current.last_completed_at is None)
            or current.status in {"retry_wait", "degraded"}
            or (current.owner_id not in {None, owner_id} and expired)
        )
        generation = current.generation + 1
        self._items[worker_name] = replace(
            current,
            status="running",
            owner_id=owner_id,
            generation=generation,
            lease_until=now + timedelta(seconds=lease_seconds),
            heartbeat_at=now,
            last_started_at=now,
        )
        return LeaseClaim(worker_name, owner_id, generation, recovered)

    def heartbeat(self, claim: LeaseClaim, *, now: datetime, lease_seconds: float) -> bool:
        current = self._owned(claim)
        if current is None:
            return False
        self._items[claim.worker_name] = replace(
            current,
            heartbeat_at=now,
            lease_until=now + timedelta(seconds=lease_seconds),
        )
        return True

    def complete(self, claim: LeaseClaim, *, now: datetime, next_run_at: datetime) -> WorkerLease:
        current = self._require_owned(claim)
        updated = replace(
            current,
            status="idle",
            owner_id=None,
            lease_until=None,
            heartbeat_at=now,
            next_run_at=next_run_at,
            last_completed_at=now,
            failure_count=0,
            last_error=None,
        )
        self._items[claim.worker_name] = updated
        return updated

    def fail(
        self,
        claim: LeaseClaim,
        *,
        now: datetime,
        next_run_at: datetime,
        error: str,
        degraded: bool,
    ) -> WorkerLease:
        current = self._require_owned(claim)
        updated = replace(
            current,
            status="degraded" if degraded else "retry_wait",
            owner_id=None,
            lease_until=None,
            heartbeat_at=now,
            next_run_at=next_run_at,
            last_completed_at=now,
            failure_count=current.failure_count + 1,
            last_error=_truncate_error(error),
        )
        self._items[claim.worker_name] = updated
        return updated

    def get(self, worker_name: str) -> WorkerLease | None:
        return self._items.get(worker_name)

    def _owned(self, claim: LeaseClaim) -> WorkerLease | None:
        current = self._items.get(claim.worker_name)
        if current is None or current.owner_id != claim.owner_id or current.generation != claim.generation:
            return None
        return current

    def _require_owned(self, claim: LeaseClaim) -> WorkerLease:
        current = self._owned(claim)
        if current is None:
            raise RuntimeError(f"automation lease lost: {claim.worker_name}")
        return current


RuntimeEventLogger = Callable[[str, dict[str, Any]], None]


class AutomationRuntime:
    def __init__(
        self,
        adapters: Sequence[AutomationAdapter],
        lease_store: AutomationLeaseStore,
        *,
        owner_id: str | None = None,
        poll_seconds: float = 1,
        now: Callable[[], datetime] | None = None,
        event_logger: RuntimeEventLogger | None = None,
    ) -> None:
        names = [adapter.name for adapter in adapters]
        if len(names) != len(set(names)):
            raise ValueError("automation adapter names must be unique")
        self.adapters = list(adapters)
        self.lease_store = lease_store
        self.owner_id = owner_id or f"worker-{uuid.uuid4().hex[:12]}"
        self.poll_seconds = max(0.01, poll_seconds)
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.event_logger = event_logger

    async def run(self, *, stop_after_one_tick: bool = False) -> None:
        while True:
            await self.tick()
            if stop_after_one_tick:
                return
            await asyncio.sleep(self.poll_seconds)

    async def tick(self) -> None:
        await asyncio.gather(*(self._run_adapter(adapter) for adapter in self.adapters))

    async def _run_adapter(self, adapter: AutomationAdapter) -> None:
        policy = adapter.policy
        now = self.now()
        try:
            claim = self.lease_store.try_acquire(
                adapter.name,
                self.owner_id,
                now=now,
                lease_seconds=policy.lease_seconds,
            )
        except Exception as error:  # noqa: BLE001 - isolate a failed lease store call.
            self._emit("worker_claim_failed", adapter, error=_truncate_error(error))
            return
        if claim is None:
            return
        self._emit("worker_lease_acquired", adapter, generation=claim.generation, recovered=claim.recovered)
        heartbeat_task = asyncio.create_task(self._heartbeat(adapter, claim))
        try:
            async def execute_tick():
                if claim.recovered:
                    recovered_items = await adapter.recover_stale()
                    self._emit("worker_recovered", adapter, recovered_items=recovered_items)
                return await adapter.run_once()

            result = await asyncio.wait_for(execute_tick(), timeout=policy.timeout_seconds)
        except TimeoutError:
            await self._safe_record_failure(adapter, claim, "worker timeout", timeout=True)
        except Exception as error:  # noqa: BLE001 - worker failure must not stop other adapters.
            await self._safe_record_failure(adapter, claim, str(error) or error.__class__.__name__)
        else:
            completed_at = self.now()
            try:
                self.lease_store.complete(
                    claim,
                    now=completed_at,
                    next_run_at=completed_at + timedelta(seconds=policy.interval_seconds),
                )
            except Exception as error:  # noqa: BLE001 - one lost lease must not stop all workers.
                self._emit("worker_lease_lost", adapter, generation=claim.generation, error=_truncate_error(error))
            else:
                self._emit("worker_completed", adapter, result=result or {})
        finally:
            heartbeat_task.cancel()
            await asyncio.gather(heartbeat_task, return_exceptions=True)

    async def _safe_record_failure(
        self,
        adapter: AutomationAdapter,
        claim: LeaseClaim,
        error: str,
        *,
        timeout: bool = False,
    ) -> None:
        try:
            await self._record_failure(adapter, claim, error, timeout=timeout)
        except Exception as store_error:  # noqa: BLE001 - preserve failure isolation on DB/lease loss.
            self._emit(
                "worker_lease_lost",
                adapter,
                generation=claim.generation,
                error=_truncate_error(store_error),
            )

    async def _record_failure(
        self,
        adapter: AutomationAdapter,
        claim: LeaseClaim,
        error: str,
        *,
        timeout: bool = False,
    ) -> None:
        policy = adapter.policy
        current = self.lease_store.get(adapter.name)
        failure_count = (current.failure_count if current is not None else 0) + 1
        degraded = failure_count >= policy.retry_budget
        delay = min(
            policy.backoff_max_seconds,
            policy.backoff_base_seconds * (2 ** max(0, failure_count - 1)),
        )
        failed_at = self.now()
        self.lease_store.fail(
            claim,
            now=failed_at,
            next_run_at=failed_at + timedelta(seconds=delay),
            error=error,
            degraded=degraded,
        )
        self._emit("worker_timeout" if timeout else "worker_failed", adapter, error=_truncate_error(error))
        self._emit("worker_degraded" if degraded else "worker_retry_scheduled", adapter, delay_seconds=delay)

    async def _heartbeat(self, adapter: AutomationAdapter, claim: LeaseClaim) -> None:
        while True:
            await asyncio.sleep(adapter.policy.heartbeat_seconds)
            try:
                alive = self.lease_store.heartbeat(
                    claim,
                    now=self.now(),
                    lease_seconds=adapter.policy.lease_seconds,
                )
            except Exception as error:  # noqa: BLE001 - heartbeat failure is lease loss.
                self._emit("worker_lease_lost", adapter, generation=claim.generation, error=_truncate_error(error))
                return
            if not alive:
                self._emit("worker_lease_lost", adapter, generation=claim.generation)
                return
            self._emit("worker_heartbeat", adapter, generation=claim.generation)

    def _emit(self, event_type: str, adapter: AutomationAdapter, **meta: Any) -> None:
        payload = {"worker": adapter.name, "owner_id": self.owner_id, **meta}
        if event_type in {"worker_failed", "worker_timeout", "worker_degraded", "worker_lease_lost", "worker_claim_failed"}:
            logger.warning("automation event=%s meta=%s", event_type, payload)
        elif event_type == "worker_completed" and (meta.get("result") or {}).get("processed", 0):
            logger.info("automation event=%s meta=%s", event_type, payload)
        elif event_type == "worker_recovered" and meta.get("recovered_items", 0):
            logger.info("automation event=%s meta=%s", event_type, payload)
        else:
            logger.debug("automation event=%s meta=%s", event_type, payload)
        if self.event_logger is not None:
            try:
                self.event_logger(event_type, payload)
            except Exception:  # noqa: BLE001 - diagnostics must not control worker availability.
                logger.exception("automation lifecycle logger failed: event=%s", event_type)


def _truncate_error(error: str, *, limit: int = 1000) -> str:
    value = str(error).strip()
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"
