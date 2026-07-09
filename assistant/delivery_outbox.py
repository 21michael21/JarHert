from __future__ import annotations

import asyncio
import logging
import re
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from enum import Enum


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
    buttons: list[list[dict[str, str]]] = field(default_factory=list)
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
    ) -> DeliveryMessage:
        now = datetime.now(timezone.utc)
        item = DeliveryMessage(
            id=self._next_id,
            user_id=user_id,
            chat_id=chat_id,
            text=text.strip(),
            trace_id=trace_id,
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
                updated_at=due_at,
            )
            self._replace(updated)
            claimed.append(updated)
        return claimed

    def mark_sent(self, message_id: int) -> DeliveryMessage:
        item = self._require(message_id)
        updated = replace(
            item,
            status=DeliveryStatus.SENT,
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
    ) -> DeliveryMessage:
        item = self._require(message_id)
        updated = replace(
            item,
            status=DeliveryStatus.QUEUED,
            last_error=_truncate_error(error),
            next_attempt_at=next_attempt_at,
            updated_at=datetime.now(timezone.utc),
        )
        self._replace(updated)
        return updated

    def mark_failed_permanent(self, message_id: int, error: str) -> DeliveryMessage:
        item = self._require(message_id)
        updated = replace(
            item,
            status=DeliveryStatus.FAILED,
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
    while True:
        due = store.claim_due(limit=limit)
        sent = 0
        retried = 0
        failed = 0
        for message in due:
            try:
                await send(message)
                store.mark_sent(message.id)
                _log_event(event_logger, message, "delivery_sent", {"attempts": message.attempts})
                sent += 1
            except Exception as error:
                classification = classify_delivery_error(error)
                error_text = _truncate_error(str(error) or error.__class__.__name__)
                if classification.retryable and message.attempts < max_attempts:
                    store.mark_retry(
                        message.id,
                        error_text,
                        _next_attempt_at(
                            attempts=message.attempts,
                            retry_after_seconds=classification.retry_after_seconds,
                        ),
                    )
                    _log_event(event_logger, message, "delivery_retry", {"error": error_text})
                    retried += 1
                else:
                    store.mark_failed_permanent(message.id, error_text)
                    _log_event(event_logger, message, "delivery_failed", {"error": error_text})
                    failed += 1
        if due:
            logger.info(
                "delivery outbox tick: due=%s sent=%s retried=%s failed=%s",
                len(due),
                sent,
                retried,
                failed,
            )
        if stop_after_one_tick:
            return
        await asyncio.sleep(interval_seconds)


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
