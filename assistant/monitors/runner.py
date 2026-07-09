from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable
from datetime import datetime, time, timezone
from typing import Any, Protocol

from assistant.automation_runtime import WorkerPolicy
from assistant.delivery_outbox import InMemoryDeliveryOutboxStore
from assistant.monitors.models import MonitorDecision, MonitorJob
from assistant.monitors.sources import MonitorSourceRegistry
from assistant.provider_clients import HermesClient
from assistant.types import HermesRequest, Intent, UserContext
from backend.store_converters import truncate_error


class MonitorJobStore(Protocol):
    def list_enabled(self, *, limit: int = 50) -> list[MonitorJob]:
        ...

    def mark_checked(
        self,
        monitor_job_id: int,
        *,
        state_hash: str,
        payload: dict[str, Any],
        checked_at: datetime | None = None,
    ) -> MonitorJob:
        ...

    def record_run(
        self,
        monitor_job_id: int,
        *,
        status: str,
        triggered: bool = False,
        message: str | None = None,
        error: str | None = None,
    ):
        ...


MonitorPayloadFetcher = Callable[[MonitorJob], dict[str, Any]]


class MonitorWorkerAdapter:
    name = "monitors"
    default_policy = WorkerPolicy(interval_seconds=900, timeout_seconds=120, lease_seconds=180, heartbeat_seconds=20)

    def __init__(
        self,
        *,
        monitor_jobs: MonitorJobStore,
        hermes: HermesClient,
        delivery_outbox,
        fetcher: MonitorPayloadFetcher | None = None,
        source_registry: MonitorSourceRegistry | None = None,
        daily_llm_budget: int | None = None,
        limit: int = 50,
        policy: WorkerPolicy | None = None,
    ) -> None:
        self.monitor_jobs = monitor_jobs
        self.hermes = hermes
        self.delivery_outbox = delivery_outbox
        self.fetcher = fetcher
        self.source_registry = source_registry
        self.daily_llm_budget = daily_llm_budget
        self.limit = limit
        self.policy = policy or self.default_policy
        self.last_result: dict[str, int] | None = None

    async def recover_stale(self) -> int:
        return 0

    async def run_once(self) -> dict:
        self.last_result = await asyncio.to_thread(
            run_monitors_once,
            monitor_jobs=self.monitor_jobs,
            hermes=self.hermes,
            delivery_outbox=self.delivery_outbox,
            fetcher=self.fetcher,
            source_registry=self.source_registry,
            daily_llm_budget=self.daily_llm_budget,
            limit=self.limit,
        )
        return self.last_result


def run_monitors_once(
    *,
    monitor_jobs: MonitorJobStore,
    hermes: HermesClient,
    delivery_outbox: InMemoryDeliveryOutboxStore,
    fetcher: MonitorPayloadFetcher | None = None,
    source_registry: MonitorSourceRegistry | None = None,
    daily_llm_budget: int | None = None,
    limit: int = 50,
) -> dict[str, int]:
    counts = {
        "checked": 0,
        "no_change": 0,
        "triggered": 0,
        "deferred": 0,
        "not_triggered": 0,
        "budget_skipped": 0,
        "failed": 0,
    }
    payload_fetcher = fetcher or (lambda job: fetch_monitor_payload(job, source_registry=source_registry))
    for job in monitor_jobs.list_enabled(limit=limit):
        counts["checked"] += 1
        try:
            payload = payload_fetcher(job)
            state_hash = hash_payload(payload)
            checked_at = datetime.now(timezone.utc)
            if job.last_state_hash == state_hash:
                monitor_jobs.mark_checked(job.id, state_hash=state_hash, payload=payload, checked_at=checked_at)
                monitor_jobs.record_run(job.id, status="no_change", triggered=False)
                counts["no_change"] += 1
                continue

            if daily_llm_budget is not None and _budget_used(monitor_jobs) >= daily_llm_budget:
                monitor_jobs.record_run(job.id, status="budget_skipped", triggered=False)
                counts["budget_skipped"] += 1
                continue

            decision = ask_monitor_decision(hermes, job, payload)
            monitor_jobs.mark_checked(job.id, state_hash=state_hash, payload=payload, checked_at=checked_at)
            if decision.triggered and decision.message:
                if _quiet_hours_active(job):
                    monitor_jobs.record_run(
                        job.id,
                        status="deferred_quiet_hours",
                        triggered=True,
                        message=decision.message,
                    )
                    counts["deferred"] += 1
                    continue
                delivery_outbox.enqueue(
                    user_id=job.user_id,
                    chat_id=job.chat_id,
                    text=decision.message,
                    trace_id=f"monitor-{job.id}",
                    idempotency_key=f"monitor:{job.id}:{state_hash}",
                )
                monitor_jobs.record_run(job.id, status="triggered", triggered=True, message=decision.message)
                counts["triggered"] += 1
            else:
                monitor_jobs.record_run(job.id, status="not_triggered", triggered=False)
                counts["not_triggered"] += 1
        except Exception as error:  # noqa: BLE001 - runner must isolate failed monitors.
            monitor_jobs.record_run(job.id, status="failed", triggered=False, error=truncate_error(str(error)))
            counts["failed"] += 1
    return counts


def fetch_monitor_payload(job: MonitorJob, *, source_registry: MonitorSourceRegistry | None = None) -> dict[str, Any]:
    return (source_registry or MonitorSourceRegistry()).fetch(job)


def run_daily_brief_once(
    *,
    monitor_jobs,
    delivery_outbox,
    limit: int = 100,
) -> dict[str, int]:
    runs = monitor_jobs.list_deferred_for_brief(limit=limit)
    grouped: dict[tuple[int, int], list] = {}
    for run in runs:
        job = monitor_jobs.get(run.monitor_job_id)
        if job is None or not run.message:
            continue
        grouped.setdefault((job.user_id, job.chat_id), []).append(run)
    briefed_ids: list[int] = []
    for (user_id, chat_id), items in grouped.items():
        lines = ["Daily Brief"]
        lines.extend(f"- {item.message}" for item in items if item.message)
        delivery_outbox.enqueue(
            user_id=user_id,
            chat_id=chat_id,
            text="\n".join(lines),
            trace_id=f"monitor-brief-{datetime.now(timezone.utc):%Y%m%d}",
            idempotency_key=f"monitor-brief:{user_id}:{datetime.now(timezone.utc):%Y-%m-%d}",
        )
        briefed_ids.extend(item.id for item in items)
    return {"briefed": monitor_jobs.mark_runs_briefed(briefed_ids)}


def hash_payload(payload: dict[str, Any]) -> str:
    normalized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def ask_monitor_decision(hermes: HermesClient, job: MonitorJob, payload: dict[str, Any]) -> MonitorDecision:
    response = hermes.ask(
        HermesRequest(
            user=UserContext(user_id=job.user_id, tg_user_id=job.chat_id),
            intent=Intent.ASK,
            prompt=build_monitor_prompt(job, payload),
            context={"response_format": "json", "monitor_job_id": str(job.id)},
            trace_id=f"monitor-{job.id}",
        )
    )
    return parse_monitor_decision(response.text)


def _budget_used(monitor_jobs) -> int:
    counter = getattr(monitor_jobs, "count_llm_runs_today", None)
    return int(counter() if counter is not None else 0)


def _quiet_hours_active(job: MonitorJob, *, now: datetime | None = None) -> bool:
    value = str(job.source_config.get("quiet_hours") or "").strip()
    if not value or "-" not in value:
        return False
    start_raw, _, end_raw = value.partition("-")
    start = _parse_clock(start_raw)
    end = _parse_clock(end_raw)
    if start is None or end is None:
        return False
    current = (now or datetime.now(timezone.utc)).time()
    if start <= end:
        return start <= current <= end
    return current >= start or current <= end


def _parse_clock(value: str) -> time | None:
    try:
        hours, minutes = [int(part) for part in value.strip().split(":", 1)]
        return time(hour=hours, minute=minutes)
    except Exception:
        return None


def build_monitor_prompt(job: MonitorJob, payload: dict[str, Any]) -> str:
    previous = job.last_payload or {}
    return "\n".join(
        [
            "Ты проверяешь proactive monitor. Ответь строго JSON без Markdown.",
            'Формат: {"triggered": boolean, "message": string|null}',
            "Если условие не выполнено, верни triggered=false и message=null.",
            "Если условие выполнено, message должен быть коротким Telegram-сообщением на русском.",
            f"Условие пользователя: {job.condition_text}",
            "Предыдущее состояние:",
            json.dumps(previous, ensure_ascii=False, sort_keys=True)[:4000],
            "Новое состояние:",
            json.dumps(payload, ensure_ascii=False, sort_keys=True)[:4000],
        ]
    )


def parse_monitor_decision(raw_text: str) -> MonitorDecision:
    data = _loads_json_object(raw_text)
    triggered = data.get("triggered")
    if not isinstance(triggered, bool):
        raise ValueError("Monitor decision must contain boolean triggered")
    message = data.get("message")
    if message is not None and not isinstance(message, str):
        raise ValueError("Monitor decision message must be string or null")
    clean_message = message.strip() if isinstance(message, str) else None
    if triggered and not clean_message:
        raise ValueError("Triggered monitor decision must include non-empty message")
    return MonitorDecision(triggered=triggered, message=clean_message)


def _loads_json_object(raw_text: str) -> dict[str, Any]:
    text = (raw_text or "").strip()
    if text.startswith("```"):
        text = text.strip("`").strip()
        if text.lower().startswith("json"):
            text = text[4:].strip()
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("Monitor decision must be a JSON object")
    return data
