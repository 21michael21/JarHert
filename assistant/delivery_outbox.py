from __future__ import annotations

import asyncio
import logging
import re
import uuid
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from enum import Enum

from assistant.automation_runtime import AutomationRuntime, InMemoryAutomationLeaseStore, LeaseLostError, WorkerPolicy
from assistant.observability import delivery_latency_ms, queue_lag_ms

logger = logging.getLogger(__name__)


class DeliveryStatus(str, Enum):
    QUEUED = "queued"
    SENDING = "sending"
    SENT = "sent"
    FAILED = "failed"


@dataclass(frozen=True)
class DeliveryMessage:
    id: int
    user_id: int
    chat_id: int
    text: str
    status: DeliveryStatus = DeliveryStatus.QUEUED
    attempts: int = 0
    trace_id: str = ""
    idempotency_key: str | None = None
    buttons: list[list[dict[str, str]]] = field(default_factory=list)
    worker_id: str | None = None
    lease_until: datetime | None = None
    claimed_at: datetime | None = None
    heartbeat_at: datetime | None = None
    last_error: str | None = None
    next_attempt_at: datetime | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class DeliveryErrorClassification:
    retryable: bool
    retry_after_seconds: int | None = None


AsyncDeliverySender = Callable[[DeliveryMessage], Awaitable[None]]
DeliveryEventLogger = Callable[[DeliveryMessage, str, dict], None]


class DeliveryOutboxAdapter:
    name = "delivery_outbox"
    default_policy = WorkerPolicy(interval_seconds=2, timeout_seconds=45, lease_seconds=75, heartbeat_seconds=15)

    def __init__(
        self,
        store,
        send: AsyncDeliverySender,
        *,
        policy: WorkerPolicy | None = None,
        limit: int = 20,
        max_attempts: int = 5,
        event_logger: DeliveryEventLogger | None = None,
        worker_id: str | None = None,
        item_lease_seconds: float = 60,
        item_heartbeat_seconds: float = 12,
    ) -> None:
        self.store = store
        self.send = send
        self.policy = policy or self.default_policy
        self.limit = limit
        self.max_attempts = max_attempts
        self.event_logger = event_logger
        self.worker_id = worker_id or f"outbox-{uuid.uuid4().hex[:12]}"
        self.item_lease_seconds = item_lease_seconds
        self.item_heartbeat_seconds = item_heartbeat_seconds
        if not 0 < item_heartbeat_seconds < item_lease_seconds:
            raise ValueError("delivery item heartbeat must be lower than item lease")

    async def recover_stale(self) -> int:
        recover = getattr(self.store, "recover_sending", None)
        return recover() if recover is not None else 0

    async def run_once(self) -> dict:
        recover_expired = getattr(self.store, "recover_expired", None)
        recovered = recover_expired() if recover_expired is not None else 0
        due = self.store.claim_due(
            limit=self.limit,
            worker_id=self.worker_id,
            lease_seconds=self.item_lease_seconds,
        )
        counts = {"processed": len(due), "sent": 0, "retried": 0, "failed": 0, "recovered": recovered}
        lease_events = {message.id: asyncio.Event() for message in due}
        heartbeat_tasks = {
            message.id: asyncio.create_task(self._heartbeat_item(message.id, lease_events[message.id]))
            for message in due
        }
        for message in due:
            try:
                if lease_events[message.id].is_set():
                    counts["lease_lost"] = counts.get("lease_lost", 0) + 1
                    continue
                await self.send(message)
                if lease_events[message.id].is_set():
                    counts["lease_lost"] = counts.get("lease_lost", 0) + 1
                    continue
                self.store.mark_sent(message.id, worker_id=self.worker_id)
                delivered_at = datetime.now(timezone.utc)
                _log_event(
                    self.event_logger,
                    message,
                    "delivery_sent",
                    {
                        "attempts": message.attempts,
                        "queue_lag_ms": queue_lag_ms(message.created_at, message.claimed_at),
                        "delivery_latency_ms": delivery_latency_ms(message.created_at, delivered_at),
                    },
                )
                counts["sent"] += 1
            except LeaseLostError:
                counts["lease_lost"] = counts.get("lease_lost", 0) + 1
                _log_event(self.event_logger, message, "delivery_lease_lost", {"worker_id": self.worker_id})
            except Exception as error:
                if lease_events[message.id].is_set():
                    counts["lease_lost"] = counts.get("lease_lost", 0) + 1
                    continue
                classification = classify_delivery_error(error)
                error_text = _truncate_error(str(error) or error.__class__.__name__)
                try:
                    if classification.retryable and message.attempts < self.max_attempts:
                        self.store.mark_retry(
                            message.id,
                            error_text,
                            _next_attempt_at(attempts=message.attempts, retry_after_seconds=classification.retry_after_seconds),
                            worker_id=self.worker_id,
                        )
                        _log_event(
                            self.event_logger,
                            message,
                            "delivery_retry",
                            {"error_type": error.__class__.__name__},
                        )
                        counts["retried"] += 1
                    else:
                        self.store.mark_failed_permanent(message.id, error_text, worker_id=self.worker_id)
                        _log_event(
                            self.event_logger,
                            message,
                            "delivery_failed",
                            {"error_type": error.__class__.__name__},
                        )
                        counts["failed"] += 1
                except LeaseLostError:
                    counts["lease_lost"] = counts.get("lease_lost", 0) + 1
                    _log_event(self.event_logger, message, "delivery_lease_lost", {"worker_id": self.worker_id})
            finally:
                heartbeat_tasks[message.id].cancel()
                await asyncio.gather(heartbeat_tasks[message.id], return_exceptions=True)
        return counts

    async def _heartbeat_item(self, message_id: int, lease_lost: asyncio.Event) -> None:
        while True:
            await asyncio.sleep(self.item_heartbeat_seconds)
            try:
                alive = await asyncio.to_thread(
                    self.store.heartbeat,
                    message_id,
                    worker_id=self.worker_id,
                    lease_seconds=self.item_lease_seconds,
                )
            except Exception:
                alive = False
            if not alive:
                lease_lost.set()
                return


class InMemoryDeliveryOutboxStore:
    def __init__(self) -> None:
        self._items: list[DeliveryMessage] = []
        self._next_id = 1

    def enqueue(
        self,
        *,
        user_id: int,
        chat_id: int,
        text: str,
        trace_id: str = "",
        buttons: list[list[dict[str, str]]] | None = None,
        next_attempt_at: datetime | None = None,
        idempotency_key: str | None = None,
    ) -> DeliveryMessage:
        if idempotency_key:
            existing = next(
                (
                    item
                    for item in self._items
                    if item.user_id == user_id and item.idempotency_key == idempotency_key
                ),
                None,
            )
            if existing is not None:
                return existing
        now = datetime.now(timezone.utc)
        item = DeliveryMessage(
            id=self._next_id,
            user_id=user_id,
            chat_id=chat_id,
            text=text.strip(),
            trace_id=trace_id,
            idempotency_key=idempotency_key,
            buttons=list(buttons or []),
            next_attempt_at=next_attempt_at,
            created_at=now,
            updated_at=now,
        )
        self._next_id += 1
        self._items.append(item)
        return item

    def claim_due(
        self,
        *,
        now: datetime | None = None,
        limit: int = 20,
        worker_id: str | None = None,
        lease_seconds: float = 60,
    ) -> list[DeliveryMessage]:
        due_at = now or datetime.now(timezone.utc)
        claimed: list[DeliveryMessage] = []
        for item in sorted(self._items, key=lambda candidate: (candidate.created_at, candidate.id)):
            if len(claimed) >= limit:
                break
            if item.status != DeliveryStatus.QUEUED:
                continue
            if item.next_attempt_at is not None and item.next_attempt_at > due_at:
                continue
            updated = replace(
                item,
                status=DeliveryStatus.SENDING,
                attempts=item.attempts + 1,
                worker_id=worker_id,
                claimed_at=due_at,
                heartbeat_at=due_at,
                lease_until=due_at + timedelta(seconds=lease_seconds),
                updated_at=due_at,
            )
            self._replace(updated)
            claimed.append(updated)
        return claimed

    def heartbeat(
        self,
        message_id: int,
        *,
        worker_id: str,
        now: datetime | None = None,
        lease_seconds: float = 60,
    ) -> bool:
        item = self._require(message_id)
        if item.status != DeliveryStatus.SENDING or item.worker_id != worker_id:
            return False
        heartbeat_at = now or datetime.now(timezone.utc)
        self._replace(
            replace(
                item,
                heartbeat_at=heartbeat_at,
                lease_until=heartbeat_at + timedelta(seconds=lease_seconds),
                updated_at=heartbeat_at,
            )
        )
        return True

    def recover_expired(self, *, now: datetime | None = None) -> int:
        expired_at = now or datetime.now(timezone.utc)
        recovered = 0
        for item in list(self._items):
            if item.status != DeliveryStatus.SENDING:
                continue
            if item.lease_until is not None and item.lease_until > expired_at:
                continue
            self._replace(
                replace(
                    item,
                    status=DeliveryStatus.QUEUED,
                    worker_id=None,
                    lease_until=None,
                    claimed_at=None,
                    heartbeat_at=None,
                    updated_at=expired_at,
                )
            )
            recovered += 1
        return recovered

    def recover_sending(self) -> int:
        recovered = 0
        for item in list(self._items):
            if item.status != DeliveryStatus.SENDING:
                continue
            self._replace(
                replace(
                    item,
                    status=DeliveryStatus.QUEUED,
                    worker_id=None,
                    lease_until=None,
                    claimed_at=None,
                    heartbeat_at=None,
                    updated_at=datetime.now(timezone.utc),
                )
            )
            recovered += 1
        return recovered

    def mark_sent(self, message_id: int, *, worker_id: str | None = None) -> DeliveryMessage:
        item = self._require(message_id)
        _assert_delivery_owner(item, worker_id)
        updated = replace(
            item,
            status=DeliveryStatus.SENT,
            lease_until=None,
            last_error=None,
            next_attempt_at=None,
            updated_at=datetime.now(timezone.utc),
        )
        self._replace(updated)
        return updated

    def mark_retry(
        self,
        message_id: int,
        error: str,
        next_attempt_at: datetime,
        *,
        worker_id: str | None = None,
    ) -> DeliveryMessage:
        item = self._require(message_id)
        _assert_delivery_owner(item, worker_id)
        updated = replace(
            item,
            status=DeliveryStatus.QUEUED,
            worker_id=None,
            lease_until=None,
            claimed_at=None,
            heartbeat_at=None,
            last_error=_truncate_error(error),
            next_attempt_at=next_attempt_at,
            updated_at=datetime.now(timezone.utc),
        )
        self._replace(updated)
        return updated

    def mark_failed_permanent(
        self,
        message_id: int,
        error: str,
        *,
        worker_id: str | None = None,
    ) -> DeliveryMessage:
        item = self._require(message_id)
        _assert_delivery_owner(item, worker_id)
        updated = replace(
            item,
            status=DeliveryStatus.FAILED,
            lease_until=None,
            last_error=_truncate_error(error),
            next_attempt_at=None,
            updated_at=datetime.now(timezone.utc),
        )
        self._replace(updated)
        return updated

    def list_recent(self, *, limit: int = 20) -> list[DeliveryMessage]:
        return sorted(self._items, key=lambda item: (item.created_at, item.id), reverse=True)[:limit]

    def stats(self) -> dict[str, int]:
        counts = Counter(item.status.value for item in self._items)
        return {status.value: counts.get(status.value, 0) for status in DeliveryStatus}

    def _require(self, message_id: int) -> DeliveryMessage:
        for item in self._items:
            if item.id == message_id:
                return item
        raise KeyError(f"delivery message not found: {message_id}")

    def _replace(self, updated: DeliveryMessage) -> None:
        for index, item in enumerate(self._items):
            if item.id == updated.id:
                self._items[index] = updated
                return
        raise KeyError(f"delivery message not found: {updated.id}")


async def run_delivery_outbox_worker(
    store,
    send: AsyncDeliverySender,
    *,
    interval_seconds: float = 2,
    stop_after_one_tick: bool = False,
    limit: int = 20,
    max_attempts: int = 5,
    event_logger: DeliveryEventLogger | None = None,
) -> None:
    adapter = DeliveryOutboxAdapter(
        store,
        send,
        policy=replace(DeliveryOutboxAdapter.default_policy, interval_seconds=interval_seconds),
        limit=limit,
        max_attempts=max_attempts,
        event_logger=event_logger,
    )
    await AutomationRuntime(
        [adapter],
        InMemoryAutomationLeaseStore(),
        poll_seconds=min(1, max(0.01, interval_seconds)),
    ).run(stop_after_one_tick=stop_after_one_tick)


def classify_delivery_error(error: Exception) -> DeliveryErrorClassification:
    text = str(error).lower()
    retry_after = _retry_after_seconds(error, text)
    if "chat not found" in text or "bot was blocked" in text or "user is deactivated" in text:
        return DeliveryErrorClassification(retryable=False)
    if retry_after is not None or "too many requests" in text or "429" in text:
        return DeliveryErrorClassification(retryable=True, retry_after_seconds=retry_after or 60)
    if isinstance(error, TimeoutError) or "timeout" in text or "timed out" in text:
        return DeliveryErrorClassification(retryable=True)
    if any(marker in text for marker in ("temporarily unavailable", "connection", "network", "502", "503", "504")):
        return DeliveryErrorClassification(retryable=True)
    return DeliveryErrorClassification(retryable=True)


def _retry_after_seconds(error: Exception, text: str) -> int | None:
    raw = getattr(error, "retry_after", None)
    if isinstance(raw, int) and raw > 0:
        return raw
    match = re.search(r"retry after\D+(\d+)", text)
    if match:
        return max(1, int(match.group(1)))
    return None


def _next_attempt_at(
    *,
    attempts: int,
    retry_after_seconds: int | None = None,
) -> datetime:
    delay = retry_after_seconds or min(300, 15 * (2 ** max(0, attempts - 1)))
    return datetime.now(timezone.utc) + timedelta(seconds=delay)


def _truncate_error(error: str, *, limit: int = 1000) -> str:
    value = str(error).strip()
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


def _log_event(logger_fn: DeliveryEventLogger | None, message: DeliveryMessage, event_type: str, meta: dict) -> None:
    if logger_fn is not None:
        logger_fn(message, event_type, meta)


def _assert_delivery_owner(message: DeliveryMessage, worker_id: str | None) -> None:
    if worker_id is None:
        return
    if message.status != DeliveryStatus.SENDING or message.worker_id != worker_id:
        raise LeaseLostError(f"delivery lease lost: message_id={message.id} worker_id={worker_id}")
