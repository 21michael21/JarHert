from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from assistant.automation_runtime import AutomationRuntime, InMemoryAutomationLeaseStore, WorkerPolicy
from assistant.provider_clients import HermesClient
from assistant.tracing import new_trace_id
from assistant.types import HermesRequest, Intent, UserContext
from backend.message_store import CollectedMessage


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TrendDecision:
    triggered: bool
    summary: str | None = None


class TelegramTrendWorkerAdapter:
    name = "telegram_trends"
    default_policy = WorkerPolicy(interval_seconds=3600, timeout_seconds=120, lease_seconds=180, heartbeat_seconds=20)

    def __init__(
        self,
        message_store,
        hermes: HermesClient,
        delivery_outbox,
        *,
        user_id: int,
        chat_id: int,
        lookback_hours: int = 6,
        limit: int = 300,
        policy: WorkerPolicy | None = None,
    ) -> None:
        self.message_store = message_store
        self.hermes = hermes
        self.delivery_outbox = delivery_outbox
        self.user_id = user_id
        self.chat_id = chat_id
        self.lookback_hours = lookback_hours
        self.limit = limit
        self.policy = policy or self.default_policy

    async def recover_stale(self) -> int:
        return 0

    async def run_once(self) -> dict:
        decision = await run_telegram_trend_once(
            self.message_store,
            self.hermes,
            self.delivery_outbox,
            user_id=self.user_id,
            chat_id=self.chat_id,
            lookback_hours=self.lookback_hours,
            limit=self.limit,
        )
        return {"processed": int(decision is not None), "triggered": bool(decision and decision.triggered)}


async def run_telegram_trend_worker(
    message_store,
    hermes: HermesClient,
    delivery_outbox,
    *,
    user_id: int,
    chat_id: int,
    interval_seconds: float = 3600,
    lookback_hours: int = 6,
    limit: int = 300,
    stop_after_one_tick: bool = False,
) -> None:
    adapter = TelegramTrendWorkerAdapter(
        message_store,
        hermes,
        delivery_outbox,
        user_id=user_id,
        chat_id=chat_id,
        lookback_hours=lookback_hours,
        limit=limit,
        policy=WorkerPolicy(
            interval_seconds=max(60, interval_seconds),
            timeout_seconds=120,
            lease_seconds=180,
            heartbeat_seconds=20,
        ),
    )
    await AutomationRuntime(
        [adapter],
        InMemoryAutomationLeaseStore(),
        poll_seconds=min(5, max(0.01, interval_seconds)),
    ).run(stop_after_one_tick=stop_after_one_tick)


async def run_telegram_trend_once(
    message_store,
    hermes: HermesClient,
    delivery_outbox,
    *,
    user_id: int,
    chat_id: int,
    lookback_hours: int = 6,
    limit: int = 300,
) -> TrendDecision | None:
    since = datetime.now(timezone.utc) - timedelta(hours=max(1, lookback_hours))
    messages = message_store.list_unprocessed(since=since, limit=limit)
    if not messages:
        return None

    text_messages = [message for message in messages if message.text.strip()]
    if not text_messages:
        message_store.mark_processed([message.id for message in messages])
        return TrendDecision(triggered=False, summary=None)

    trace_id = f"telegram-trends-{new_trace_id()}"
    request = HermesRequest(
        user=UserContext(user_id=user_id, tg_user_id=chat_id),
        prompt=build_trend_prompt(text_messages),
        intent=Intent.ASK,
        context={"response_format": "json", "job_type": "telegram_trendwatch"},
        trace_id=trace_id,
    )
    response = await asyncio.to_thread(hermes.ask, request)
    decision = parse_trend_decision(response.text)
    if decision.triggered and decision.summary:
        delivery_outbox.enqueue(
            user_id=user_id,
            chat_id=chat_id,
            text=decision.summary.strip(),
            trace_id=trace_id,
        )
    message_store.mark_processed([message.id for message in messages])
    logger.info(
        "telegram trend worker tick: messages=%s triggered=%s provider=%s latency_ms=%s",
        len(messages),
        decision.triggered,
        response.provider,
        response.latency_ms,
    )
    return decision


def build_trend_prompt(messages: list[CollectedMessage]) -> str:
    return "\n".join(
        [
            "Ты анализируешь новые сообщения из выбранных Telegram-чатов.",
            "Ответь строго JSON без Markdown.",
            'Схема: {"triggered": boolean, "summary": string|null}.',
            "triggered=true только если есть заметная новая тема, повторяющийся паттерн или важный сигнал.",
            "Если явного тренда нет, верни triggered=false и summary=null.",
            "Если triggered=true, summary должен быть коротким Telegram-сообщением на русском: 3–7 пунктов максимум.",
            "Не пересказывай каждое сообщение. Не цитируй личные данные без необходимости.",
            "Сообщения:",
            _format_messages(messages),
        ]
    )


def parse_trend_decision(text: str) -> TrendDecision:
    payload = json.loads(_strip_json_fence(text))
    if not isinstance(payload, dict):
        raise ValueError("trend decision must be a JSON object")
    triggered = payload.get("triggered")
    if not isinstance(triggered, bool):
        raise ValueError("trend decision.triggered must be boolean")
    summary = payload.get("summary")
    if summary is not None and not isinstance(summary, str):
        raise ValueError("trend decision.summary must be string or null")
    return TrendDecision(triggered=triggered, summary=summary)


def _format_messages(messages: list[CollectedMessage], *, max_chars: int = 12000) -> str:
    lines: list[str] = []
    for message in messages:
        sender = message.sender_name or str(message.sender_id or "unknown")
        chat = message.chat_title or str(message.chat_id)
        text = " ".join(message.text.split())
        if not text:
            continue
        lines.append(f"[{message.timestamp.isoformat()}] {chat} / {sender}: {_truncate(text, 700)}")
    value = "\n".join(lines)
    return _truncate(value, max_chars)


def _strip_json_fence(text: str) -> str:
    clean = (text or "").strip()
    match = re.fullmatch(r"```(?:json)?\s*(.+?)\s*```", clean, flags=re.IGNORECASE | re.DOTALL)
    if match:
        return match.group(1).strip()
    return clean


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"
