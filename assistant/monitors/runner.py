from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, Protocol

from assistant.delivery_outbox import InMemoryDeliveryOutboxStore
from assistant.monitors.github_releases import fetch_latest_github_release
from assistant.monitors.models import MonitorDecision, MonitorJob
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
        error: str | None = None,
    ):
        ...


MonitorPayloadFetcher = Callable[[MonitorJob], dict[str, Any]]


def run_monitors_once(
    *,
    monitor_jobs: MonitorJobStore,
    hermes: HermesClient,
    delivery_outbox: InMemoryDeliveryOutboxStore,
    fetcher: MonitorPayloadFetcher | None = None,
    limit: int = 50,
) -> dict[str, int]:
    counts = {"checked": 0, "no_change": 0, "triggered": 0, "not_triggered": 0, "failed": 0}
    payload_fetcher = fetcher or fetch_monitor_payload
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

            decision = ask_monitor_decision(hermes, job, payload)
            monitor_jobs.mark_checked(job.id, state_hash=state_hash, payload=payload, checked_at=checked_at)
            if decision.triggered and decision.message:
                delivery_outbox.enqueue(
                    user_id=job.user_id,
                    chat_id=job.chat_id,
                    text=decision.message,
                    trace_id=f"monitor-{job.id}",
                )
                monitor_jobs.record_run(job.id, status="triggered", triggered=True)
                counts["triggered"] += 1
            else:
                monitor_jobs.record_run(job.id, status="not_triggered", triggered=False)
                counts["not_triggered"] += 1
        except Exception as error:  # noqa: BLE001 - runner must isolate failed monitors.
            monitor_jobs.record_run(job.id, status="failed", triggered=False, error=truncate_error(str(error)))
            counts["failed"] += 1
    return counts


def fetch_monitor_payload(job: MonitorJob) -> dict[str, Any]:
    if job.source_type != "github_releases":
        raise ValueError(f"Unsupported monitor source_type: {job.source_type}")
    owner = str(job.source_config.get("owner") or "")
    repo = str(job.source_config.get("repo") or "")
    return fetch_latest_github_release(owner, repo)


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
