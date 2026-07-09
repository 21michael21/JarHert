from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
FAKE_PROVIDERS = {"fake", "fake-hermes", "local-e2e", "deterministic"}


@dataclass
class StepResult:
    name: str
    status: str
    detail: str = ""
    latency_ms: int = 0
    trace_id: str = ""
    provider: str = ""
    model: str = ""
    blocked_reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class RunReport:
    mode: str
    run_id: str
    started_at: str
    steps: list[StepResult] = field(default_factory=list)
    finished_at: str = ""
    elapsed_ms: int = 0
    require_live: bool = False
    strict_failure: bool = False
    exit_code: int = 0

    @property
    def ok(self) -> bool:
        return all(step.status != "failed" for step in self.steps) and not self.strict_failure

    def payload(self) -> dict[str, Any]:
        counts = {status: sum(step.status == status for step in self.steps) for status in ("passed", "failed", "skipped")}
        return {
            "mode": self.mode,
            "run_id": self.run_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "elapsed_ms": self.elapsed_ms,
            "ok": self.ok,
            "strict": {"require_live": self.require_live, "passed": not self.strict_failure},
            "exit_code": self.exit_code,
            "summary": counts,
            "steps": [_step_payload(step, self.run_id) for step in self.steps],
        }


class LocalHermes:
    def __init__(self, *, monitor_triggered: bool = True) -> None:
        self.monitor_triggered = monitor_triggered
        self.requests = []

    def ask(self, request):
        from assistant.types import HermesResponse

        self.requests.append(request)
        if request.context.get("response_format") == "json":
            value = {"triggered": self.monitor_triggered, "message": "Вышел важный тестовый релиз." if self.monitor_triggered else None}
            return HermesResponse(text=json.dumps(value, ensure_ascii=False), provider="local-e2e", model="deterministic")
        return HermesResponse(text="Проверка полного пути выполнена.", provider="local-e2e", model="deterministic")


class LocalTaskCenter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    def create_task(self, text: str) -> str:
        self.calls.append(("task", text))
        return "Создал Trello card id=e2e-card"

    def list_tasks(self, text: str) -> str:
        self.calls.append(("list", text))
        return "Тестовая очередь"

    def create_calendar_event(self, text: str) -> str:
        self.calls.append(("calendar", text))
        return "Создал Calendar event id=e2e-event"

    def create_task_with_calendar(self, **kwargs) -> str:
        self.calls.append(("task_with_calendar", kwargs))
        return "Создал Trello card и Calendar event"


@dataclass
class Runtime:
    factory: Any
    service: Any
    users: Any
    actions: Any
    outbox: Any
    reminders: Any
    monitors: Any
    events: Any
    hermes: Any
    db_user: Any
    tg_user_id: int


def evaluate_exit_code(report: RunReport, *, require_live: bool) -> int:
    strict_failure = report.mode != "live" if require_live else False
    for step in report.steps:
        strict_failure |= step.status == "failed"
        if require_live:
            strict_failure |= step.status == "skipped" or bool(step.blocked_reason)
            strict_failure |= step.provider.lower() in FAKE_PROVIDERS
            if step.metadata.get("requires_real_provider"):
                strict_failure |= step.provider.lower() in FAKE_PROVIDERS or not step.provider
    report.require_live = require_live
    report.strict_failure = strict_failure
    report.exit_code = int(strict_failure)
    return report.exit_code


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    started = time.perf_counter()
    report = RunReport(args.mode, uuid.uuid4().hex[:12], _now())
    report_path = Path(args.report_path) if args.report_path else _default_report_path(report.run_id)
    try:
        _validate_args(args)
        if args.mode == "live":
            _run_live_preflight(report, args)
        with tempfile.TemporaryDirectory(prefix="jarhert-system-e2e-") as tmp:
            runtime = _build_runtime(Path(tmp) / "e2e.sqlite3", args, report)
            _run_component_cycle(report, runtime, args)
        if args.mode == "live":
            asyncio.run(_run_live_telegram(report, args))
    except Exception as error:  # noqa: BLE001 - report must survive all test failures.
        report.steps.append(StepResult("runner_exception", "failed", _safe_error(error)))
    finally:
        report.finished_at = _now()
        report.elapsed_ms = int((time.perf_counter() - started) * 1000)
        evaluate_exit_code(report, require_live=args.require_live)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report.payload(), ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report.payload()["summary"], ensure_ascii=False), f"report={report_path}")
    return report.exit_code


def _build_runtime(db_path: Path, args, report: RunReport) -> Runtime:
    from assistant.pipeline import AssistantPipeline
    from backend.db import init_db, make_session_factory
    from backend.stores import (
        EventStore, SqlActionQueueStore, SqlAgentJobStore, SqlConversationStore, SqlDailyLimitStore,
        SqlDeliveryOutboxStore, SqlIdeaStore, SqlMemoryStore, SqlMonitorJobStore, SqlProviderHealthStore,
        SqlReminderStore, SqlTraceStore, SqlUserPreferenceStore, UserStore,
    )
    from gateway_bot.service import GatewayService

    factory = make_session_factory(f"sqlite:///{db_path}")
    init_db(factory)
    hermes = LocalHermes() if args.mode == "local" else _real_hermes(report)
    users, events = UserStore(factory), EventStore(factory)
    actions, outbox = SqlActionQueueStore(factory), SqlDeliveryOutboxStore(factory)
    reminders, monitors = SqlReminderStore(factory), SqlMonitorJobStore(factory)
    pipeline = AssistantPipeline(
        hermes, SqlDailyLimitStore(factory, per_user_limit=100, global_limit=500), plain_text_ai_enabled=True,
        memories=SqlMemoryStore(factory), ideas=SqlIdeaStore(factory), reminders=reminders,
        task_center=LocalTaskCenter(), agent_jobs=SqlAgentJobStore(factory), action_queue=actions,
        conversation_turns=SqlConversationStore(factory), preferences=SqlUserPreferenceStore(factory),
        provider_health=SqlProviderHealthStore(factory), delivery_outbox=outbox, events=events, monitor_jobs=monitors,
    )
    service = GatewayService(
        pipeline, allowed_tg_user_ids={args.tg_user_id, args.tg_user_id + 1}, admin_tg_user_ids={args.tg_user_id},
        users=users, events=events, traces=SqlTraceStore(factory),
    )
    return Runtime(factory, service, users, actions, outbox, reminders, monitors, events, hermes, users.get_or_create(args.tg_user_id), args.tg_user_id)


def _run_component_cycle(report: RunReport, runtime: Runtime, args) -> None:
    _step(report, "telegram_text_to_llm", lambda: _reply_result(runtime.service.handle_text(runtime.tg_user_id, f"/ask системная проверка {report.run_id}"), True))
    if args.mode != "live":
        _voice_step(report, runtime, args)
    ownership_result: dict[str, Any] = {}
    _action_flow(report, runtime, f"/task e2e-{report.run_id} | list=Today", "task_approval_callback", ownership_result)
    _action_flow(
        report, runtime,
        f"/calendar e2e-{report.run_id} | start=2026-07-10 10:00 | end=2026-07-10 10:30",
        "calendar_action_worker", ownership_result,
    )
    _step(report, "ownership", lambda: ownership_result or _fail("ownership callback was not exercised"))
    _reminder_flow(report, runtime)
    _provider_fallback(report, runtime, args)
    _queue_invariants(report, runtime)
    _monitor_flows(report, runtime)
    _drain_outbox(report, runtime)


def _voice_step(report: RunReport, runtime: Runtime, args) -> None:
    transcript = "завтра задача один проверить голосовой сценарий в 10:00, задача два созвон в 12:00"
    if args.mode != "local":
        if not args.voice_file:
            report.steps.append(StepResult("voice_to_natural_action", "skipped", "--voice-file is required for real STT"))
            return
        from assistant.transcription import OpenAITranscriber
        from backend.config import Settings

        settings = Settings()
        audio_path = Path(args.voice_file)
        transcript = OpenAITranscriber(settings.openai_api_key, model=settings.openai_transcribe_model).transcribe(
            audio_path.read_bytes(), filename=audio_path.name,
        )
    def run() -> dict[str, Any]:
        reply = runtime.service.handle_text(runtime.tg_user_id, transcript)
        result = _reply_result(reply, False)
        callback = _callback(reply, "confirm_job")
        if not callback:
            raise AssertionError("voice transcript did not reach natural action queue")
        runtime.service.cancel_job(runtime.tg_user_id, int(callback.rsplit(":", 1)[1]))
        return {**result, "metadata": {"transcript_chars": len(transcript), "stt": args.mode != "local"}}
    _step(report, "voice_to_natural_action", run)


def _action_flow(report: RunReport, runtime: Runtime, command: str, name: str, ownership_result: dict[str, Any]) -> None:
    from assistant.action_queue import ActionStatus
    from assistant.action_worker import run_action_worker
    from assistant.types import UserContext
    from gateway_bot.telegram_callbacks import handle_callback_data

    def run() -> dict[str, Any]:
        reply = runtime.service.handle_text(runtime.tg_user_id, command)
        callback = _callback(reply, "confirm_job")
        if reply.blocked_reason or not callback:
            raise AssertionError(reply.blocked_reason or "approval callback missing")
        foreign = handle_callback_data(runtime.service, runtime.tg_user_id + 1, callback)
        if not foreign.blocked_reason:
            raise AssertionError("foreign user confirmed owned job")
        ownership_result.update({"trace_id": reply.trace_id, "metadata": {"foreign_blocked": True}})
        confirmed = handle_callback_data(runtime.service, runtime.tg_user_id, callback)
        if confirmed.blocked_reason:
            raise AssertionError(confirmed.blocked_reason)
        delivered: list[int] = []
        async def execute(action):
            return runtime.service.pipeline.execute_queued_action_result(
                UserContext(runtime.db_user.id, runtime.tg_user_id), action,
            )
        async def deliver(action, text: str):
            delivered.append(runtime.outbox.enqueue(user_id=action.user_id, chat_id=runtime.tg_user_id, text=text, trace_id=action.trace_id).id)
        asyncio.run(run_action_worker(runtime.actions, execute, deliver, stop_after_one_tick=True))
        actions = runtime.actions.list_for_user(runtime.db_user.id, limit=50)
        owned = [item for item in actions if item.trace_id == reply.trace_id]
        if not owned or owned[0].status != ActionStatus.SUCCEEDED or not delivered:
            raise AssertionError("confirmed action was not executed and delivered")
        return {"trace_id": reply.trace_id, "metadata": {"action_ids": [item.id for item in owned], "delivery_ids": delivered}}
    _step(report, name, run)


def _reminder_flow(report: RunReport, runtime: Runtime) -> None:
    from reminders.worker import run_reminder_worker

    def run() -> dict[str, Any]:
        when = (datetime.now(timezone.utc) - timedelta(minutes=2)).strftime("%Y-%m-%d %H:%M")
        reply = runtime.service.handle_text(runtime.tg_user_id, f"/remind {when} e2e-{uuid.uuid4().hex[:6]}")
        if reply.blocked_reason:
            raise AssertionError(reply.blocked_reason)
        queued: list[int] = []
        async def send(reminder):
            queued.append(runtime.outbox.enqueue(user_id=reminder.user_id, chat_id=runtime.tg_user_id, text=reminder.text, trace_id=f"reminder-{reminder.id}").id)
        asyncio.run(run_reminder_worker(runtime.reminders, send, stop_after_one_tick=True))
        if not queued:
            raise AssertionError("due reminder did not reach delivery outbox")
        return {"trace_id": reply.trace_id, "metadata": {"delivery_ids": queued}}
    _step(report, "reminder_to_outbox", run)


def _provider_fallback(report: RunReport, runtime: Runtime, args) -> None:
    from assistant.provider_clients import FakeHermesClient
    from assistant.provider_diagnostics import HermesClientError
    from assistant.provider_fallback import FallbackHermesClient
    from assistant.types import HermesRequest, UserContext

    def run() -> dict[str, Any]:
        client = FallbackHermesClient([
            FakeHermesClient([HermesClientError("forced primary failure")]),
            runtime.hermes,
        ])
        response = client.ask(HermesRequest(UserContext(runtime.db_user.id, runtime.tg_user_id), "fallback probe"))
        if response.fallback_count != 1:
            raise AssertionError("provider fallback was not used")
        return {
            "provider": response.provider,
            "model": response.model,
            "metadata": {"fallback_count": response.fallback_count, "requires_real_provider": args.mode != "local"},
        }
    _step(report, "provider_fallback", run)


def _queue_invariants(report: RunReport, runtime: Runtime) -> None:
    from assistant.action_schema import ActionType
    from backend.stores import SqlActionQueueStore

    def duplicate() -> dict[str, Any]:
        kwargs = dict(user_id=runtime.db_user.id, action_type=ActionType.IDEA_SAVE, payload={"text": "dedup"}, idempotency_key=f"e2e:{uuid.uuid4().hex}")
        first, second = runtime.actions.enqueue(**kwargs), runtime.actions.enqueue(**kwargs)
        if first.id != second.id:
            raise AssertionError("idempotency key created duplicate action")
        runtime.actions.mark_succeeded(first.id)
        return {"trace_id": first.trace_id, "metadata": {"action_id": first.id, "scope": "action_queue_not_telegram_update"}}
    _step(report, "duplicate_action_idempotency", duplicate)

    def restart() -> dict[str, Any]:
        action = runtime.actions.enqueue(user_id=runtime.db_user.id, action_type=ActionType.IDEA_SAVE, payload={"text": "restart"}, trace_id=f"restart-{uuid.uuid4().hex[:8]}")
        recovered = next((item for item in SqlActionQueueStore(runtime.factory).list_for_user(runtime.db_user.id, limit=100) if item.id == action.id), None)
        if recovered is None:
            raise AssertionError("queued action was not recovered by a fresh store")
        runtime.actions.mark_succeeded(action.id)
        return {"trace_id": action.trace_id, "metadata": {"action_id": action.id, "scope": "queued_persistence"}}
    _step(report, "queued_action_restart", restart)


def _monitor_flows(report: RunReport, runtime: Runtime) -> None:
    from assistant.monitors.runner import hash_payload, run_monitors_once

    def triggered() -> dict[str, Any]:
        job = runtime.monitors.create(user_id=runtime.db_user.id, chat_id=runtime.tg_user_id, source_type="github_releases", source_config={"owner": "openai", "repo": "codex"}, condition_text="срабатывай при любом изменении")
        payload = {"tag_name": f"v-{uuid.uuid4().hex[:6]}", "name": "E2E release"}
        summary = run_monitors_once(monitor_jobs=runtime.monitors, hermes=runtime.hermes, delivery_outbox=runtime.outbox, fetcher=lambda _: payload)
        runtime.monitors.disable_for_user(runtime.db_user.id, job.id)
        if summary["triggered"] != 1:
            raise AssertionError(f"monitor did not trigger: {summary}")
        return {"trace_id": f"monitor-{job.id}", "metadata": summary}
    _step(report, "monitor_triggered", triggered)

    def no_change() -> dict[str, Any]:
        payload = {"tag_name": "stable", "name": "Stable release"}
        job = runtime.monitors.create(user_id=runtime.db_user.id, chat_id=runtime.tg_user_id, source_type="github_releases", source_config={"owner": "openai", "repo": "codex"}, condition_text="важный релиз")
        runtime.monitors.mark_checked(job.id, state_hash=hash_payload(payload), payload=payload)
        before = len(getattr(runtime.hermes, "requests", []))
        summary = run_monitors_once(monitor_jobs=runtime.monitors, hermes=runtime.hermes, delivery_outbox=runtime.outbox, fetcher=lambda _: payload)
        after = len(getattr(runtime.hermes, "requests", []))
        if summary["no_change"] != 1 or after != before:
            raise AssertionError("no-change monitor called LLM or returned wrong status")
        return {"trace_id": f"monitor-{job.id}", "metadata": summary}
    _step(report, "monitor_no_change", no_change)


def _drain_outbox(report: RunReport, runtime: Runtime) -> None:
    from assistant.delivery_outbox import run_delivery_outbox_worker

    def run() -> dict[str, Any]:
        sent: list[int] = []
        async def send(message):
            sent.append(message.id)
        asyncio.run(run_delivery_outbox_worker(runtime.outbox, send, stop_after_one_tick=True, limit=100))
        if not sent:
            raise AssertionError("delivery outbox had no final messages")
        return {"metadata": {"sent_ids": sent, "count": len(sent)}}
    _step(report, "delivery_outbox_final", run)


def _run_live_preflight(report: RunReport, args) -> None:
    from backend.config import Settings
    settings = Settings()
    missing = []
    for key, value in {
        "BOT_TOKEN": settings.bot_token, "TELEGRAM_API_ID": os.getenv("TELEGRAM_API_ID"),
        "TELEGRAM_API_HASH": os.getenv("TELEGRAM_API_HASH"), "voice_file": args.voice_file,
    }.items():
        if not value:
            missing.append(key)
    if settings.hermes_mode == "fake":
        missing.append("real HERMES_MODE")
    if not settings.task_command_center_enabled or not settings.task_command_center_dir:
        missing.append("TASK_COMMAND_CENTER")
    elif not Path(settings.task_command_center_dir).is_dir():
        missing.append("existing TASK_COMMAND_CENTER_DIR")
    if missing:
        raise RuntimeError("live preflight missing: " + ", ".join(missing))
    report.steps.append(StepResult("live_preflight", "passed", f"hermes_mode={settings.hermes_mode}"))


async def _run_live_telegram(report: RunReport, args) -> None:
    from scripts.live_system_telegram import run_live_telegram

    for values in await run_live_telegram(args=args, run_id=report.run_id):
        report.steps.append(StepResult(**values))


def _step(report: RunReport, name: str, function: Callable[[], dict[str, Any]]) -> None:
    started = time.perf_counter()
    try:
        values = function() or {}
        nested_meta = values.pop("metadata", {})
        report.steps.append(StepResult(name=name, status="passed", latency_ms=int((time.perf_counter() - started) * 1000), metadata=nested_meta, **values))
    except Exception as error:  # noqa: BLE001 - each scenario must be isolated in the report.
        report.steps.append(StepResult(name, "failed", _safe_error(error), int((time.perf_counter() - started) * 1000)))


def _reply_result(reply, requires_real_provider: bool) -> dict[str, Any]:
    if reply.blocked_reason:
        raise AssertionError(reply.blocked_reason)
    return {
        "trace_id": reply.trace_id, "provider": reply.provider or "", "model": reply.model or "",
        "metadata": {"intent": reply.intent.value, "fallback_count": reply.fallback_count, "requires_real_provider": requires_real_provider},
    }


def _real_hermes(report: RunReport):
    from backend.config import Settings
    from gateway_bot.main import build_hermes_client
    settings = Settings()
    if settings.hermes_mode == "fake":
        report.steps.append(StepResult("real_provider_preflight", "failed", "HERMES_MODE=fake", provider="fake", metadata={"requires_real_provider": True}))
        return LocalHermes()
    return build_hermes_client()


def _callback(reply, kind: str) -> str:
    for row in reply.buttons:
        for button in row:
            if f"ai:{kind}:" in button.callback_data:
                return button.callback_data
    return ""


def _fail(message: str):
    raise AssertionError(message)


def _safe_error(error: Exception) -> str:
    return f"{error.__class__.__name__}: {str(error)[:500]}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_report_path(run_id: str) -> Path:
    return PROJECT_ROOT / "reports" / "live-system-e2e" / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{run_id}.json"


def _step_payload(step: StepResult, run_id: str) -> dict[str, Any]:
    payload = asdict(step)
    payload["trace_id"] = payload["trace_id"] or f"e2e-{run_id}-{step.name}"
    return payload


def _validate_args(args) -> None:
    if args.require_live and args.mode != "live":
        raise ValueError("--require-live requires --mode live")
    if args.voice_file and not Path(args.voice_file).is_file():
        raise ValueError(f"voice fixture not found: {args.voice_file}")


def _parse_args(argv: list[str] | None):
    parser = argparse.ArgumentParser(description="Strict whole-system JarHert E2E proof")
    parser.add_argument("--mode", choices=("local", "sandbox", "live"), default="local")
    parser.add_argument("--tg-user-id", type=int, default=566055009)
    parser.add_argument("--report-path", default="")
    parser.add_argument("--require-live", action="store_true")
    parser.add_argument("--voice-file", default="")
    parser.add_argument("--bot-username", default="")
    parser.add_argument("--telethon-session", default=os.getenv("LIVE_E2E_TELETHON_SESSION", "./data/live_e2e_user.session"))
    parser.add_argument("--live-timeout", type=float, default=90.0)
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
