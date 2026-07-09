from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


@dataclass
class StepResult:
    name: str
    ok: bool
    detail: str
    elapsed_ms: int = 0


class TelegramApiError(RuntimeError):
    def __init__(self, message: str, *, retry_after: int | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


def main() -> int:
    args = _parse_args()
    if not args.use_main_db:
        e2e_db = PROJECT_ROOT / "data" / "live_e2e.sqlite3"
        if not args.keep_e2e_db and e2e_db.exists():
            e2e_db.unlink()
        os.environ["DATABASE_URL"] = f"sqlite:///{e2e_db}"
    if not args.allow_doc_sync:
        os.environ["ENABLE_GOOGLE_SHEETS_SYNC"] = "false"
        os.environ["GOOGLE_DOCS_WEBHOOK_URL"] = ""

    from assistant.action_queue import ActionStatus
    from assistant.action_worker import run_action_worker
    from assistant.transcription import OpenAITranscriber, TranscriptionError
    from assistant.types import UserContext
    from backend.stores import EventStore, SqlActionQueueStore, SqlDeliveryOutboxStore, SqlReminderStore, UserStore
    from gateway_bot.main import get_gateway_service, get_session_factory, settings
    from reminders.worker import run_reminder_worker
    from scripts.run_migrations import run_migrations

    started = time.perf_counter()
    run_migrations()
    factory = get_session_factory()
    service = get_gateway_service()
    users = UserStore(factory)
    db_user = users.get_or_create(args.tg_user_id)
    outbox = SqlDeliveryOutboxStore(factory)
    reminders = SqlReminderStore(factory)
    actions = SqlActionQueueStore(factory)
    events = EventStore(factory)
    results: list[StepResult] = []

    if args.require_real_llm and settings.hermes_mode == "fake":
        results.append(StepResult("preflight", False, "HERMES_MODE=fake, real LLM required"))
        _print_summary(results, started)
        return 1
    if args.send_telegram and not settings.bot_token:
        results.append(StepResult("preflight", False, "BOT_TOKEN is required for Telegram delivery"))
        _print_summary(results, started)
        return 1

    results.append(StepResult("preflight", True, f"db={'main' if args.use_main_db else 'isolated'} hermes_mode={settings.hermes_mode}"))

    ask_reply = _timed(
        "text_llm",
        lambda: service.handle_text(args.tg_user_id, args.ask_text),
    )
    results.append(
        StepResult(
            "text_llm",
            ask_reply.ok,
            f"intent={ask_reply.value.intent.value} provider={ask_reply.value.provider or 'none'} blocked={ask_reply.value.blocked_reason or 'none'}",
            ask_reply.elapsed_ms,
        )
    )
    if args.send_telegram:
        message = outbox.enqueue(user_id=db_user.id, chat_id=args.tg_user_id, text=f"[live-e2e] LLM: {ask_reply.value.text}")
        results.append(_deliver_one(outbox, message, settings.bot_token, events=events))

    reminder_text = _due_reminder_text(args.reminder_text)
    reminder_reply = _timed("reminder_create", lambda: service.handle_text(args.tg_user_id, reminder_text))
    results.append(
        StepResult(
            "reminder_create",
            reminder_reply.ok and reminder_reply.value.blocked_reason is None,
            _compact(reminder_reply.value.text),
            reminder_reply.elapsed_ms,
        )
    )
    reminder_outbox_ids: list[int] = []

    async def enqueue_reminder(reminder) -> None:
        message = outbox.enqueue(
            user_id=reminder.user_id,
            chat_id=args.tg_user_id,
            text=f"[live-e2e] Напоминание #{reminder.id}: {reminder.text}",
            trace_id=f"reminder-{reminder.id}",
        )
        events.log(
            reminder.user_id,
            "delivery_queued",
            {"reminder_id": reminder.id, "delivery_id": message.id},
            trace_id=f"reminder-{reminder.id}",
        )
        reminder_outbox_ids.append(message.id)

    asyncio.run(run_reminder_worker(reminders, enqueue_reminder, stop_after_one_tick=True))
    results.append(
        StepResult(
            "reminder_worker",
            bool(reminder_outbox_ids),
            f"queued_delivery_ids={','.join(map(str, reminder_outbox_ids)) or 'none'}",
        )
    )
    if args.send_telegram:
        for message in outbox.list_recent(limit=20):
            if message.id in reminder_outbox_ids:
                results.append(_deliver_one(outbox, message, settings.bot_token, events=events))

    if args.include_task:
        before_action_ids = _action_ids(actions, db_user.id)
        task_reply = _timed("task_fast_ack", lambda: service.handle_text(args.tg_user_id, args.task_text))
        task_action_ids = sorted(_action_ids(actions, db_user.id) - before_action_ids)
        results.append(
            StepResult(
                "task_fast_ack",
                task_reply.ok and "Job #" in task_reply.value.text and bool(task_action_ids),
                f"{_compact(task_reply.value.text)} action_ids={','.join(map(str, task_action_ids)) or 'none'}",
                task_reply.elapsed_ms,
            )
        )
        if args.send_telegram:
            message = outbox.enqueue(
                user_id=db_user.id,
                chat_id=args.tg_user_id,
                text=f"[live-e2e] Task: {task_reply.value.text}",
                trace_id=task_reply.value.trace_id,
                buttons=_buttons_to_payload(task_reply.value.buttons),
            )
            results.append(_deliver_one(outbox, message, settings.bot_token, events=events))
        confirm_result = _confirm_actions(service, args.tg_user_id, actions, db_user.id, task_action_ids)
        results.append(confirm_result)
        action_outbox_ids: list[int] = []

        async def execute_action(action) -> str:
            return service.pipeline.execute_queued_action(
                UserContext(user_id=db_user.id, tg_user_id=args.tg_user_id),
                action,
            )

        async def deliver_action_result(action, text: str) -> None:
            message = outbox.enqueue(
                user_id=action.user_id,
                chat_id=args.tg_user_id,
                text=f"[live-e2e] {text}",
                trace_id=action.trace_id,
                buttons=[[{"text": "Статус job", "callback_data": f"ai:status:{action.job_id}"}]] if action.job_id else [],
            )
            events.log(
                action.user_id,
                "delivery_queued",
                {"action_id": action.id, "job_id": action.job_id, "delivery_id": message.id},
                trace_id=action.trace_id,
            )
            action_outbox_ids.append(message.id)

        asyncio.run(
            run_action_worker(
                actions,
                execute_action,
                deliver_action_result,
                stop_after_one_tick=True,
                event_logger=lambda action, event_type, meta: events.log(
                    action.user_id,
                    event_type,
                    {"action_id": action.id, "job_id": action.job_id, **meta},
                    trace_id=action.trace_id,
                ),
            )
        )
        task_statuses = _action_statuses(actions, db_user.id, task_action_ids)
        results.append(
            StepResult(
                "action_worker",
                bool(action_outbox_ids) and all(status == ActionStatus.SUCCEEDED.value for status in task_statuses.values()),
                (
                    f"statuses={task_statuses or {}} "
                    f"queued_delivery_ids={','.join(map(str, action_outbox_ids)) or 'none'}"
                ),
            )
        )
        if args.send_telegram:
            for message in outbox.list_recent(limit=20):
                if message.id in action_outbox_ids:
                    results.append(_deliver_one(outbox, message, settings.bot_token, events=events))

    if args.include_calendar:
        before_action_ids = _action_ids(actions, db_user.id)
        calendar_reply = _timed("calendar_fast_ack", lambda: service.handle_text(args.tg_user_id, args.calendar_text))
        calendar_action_ids = sorted(_action_ids(actions, db_user.id) - before_action_ids)
        results.append(
            StepResult(
                "calendar_fast_ack",
                calendar_reply.ok and "Job #" in calendar_reply.value.text and bool(calendar_action_ids),
                f"{_compact(calendar_reply.value.text)} action_ids={','.join(map(str, calendar_action_ids)) or 'none'}",
                calendar_reply.elapsed_ms,
            )
        )
        if args.send_telegram:
            message = outbox.enqueue(
                user_id=db_user.id,
                chat_id=args.tg_user_id,
                text=f"[live-e2e-calendar] {calendar_reply.value.text}",
                trace_id=calendar_reply.value.trace_id,
                buttons=_buttons_to_payload(calendar_reply.value.buttons),
            )
            results.append(_deliver_one(outbox, message, settings.bot_token, events=events))
        confirm_result = _confirm_actions(service, args.tg_user_id, actions, db_user.id, calendar_action_ids)
        results.append(StepResult("calendar_action_confirm", confirm_result.ok, confirm_result.detail, confirm_result.elapsed_ms))
        calendar_outbox_ids: list[int] = []

        async def execute_calendar_action(action) -> str:
            return service.pipeline.execute_queued_action(
                UserContext(user_id=db_user.id, tg_user_id=args.tg_user_id),
                action,
            )

        async def deliver_calendar_result(action, text: str) -> None:
            message = outbox.enqueue(
                user_id=action.user_id,
                chat_id=args.tg_user_id,
                text=f"[live-e2e-calendar] {text}",
                trace_id=action.trace_id,
                buttons=[[{"text": "Статус job", "callback_data": f"ai:status:{action.job_id}"}]] if action.job_id else [],
            )
            events.log(
                action.user_id,
                "delivery_queued",
                {"action_id": action.id, "job_id": action.job_id, "delivery_id": message.id},
                trace_id=action.trace_id,
            )
            calendar_outbox_ids.append(message.id)

        asyncio.run(
            run_action_worker(
                actions,
                execute_calendar_action,
                deliver_calendar_result,
                stop_after_one_tick=True,
                event_logger=lambda action, event_type, meta: events.log(
                    action.user_id,
                    event_type,
                    {"action_id": action.id, "job_id": action.job_id, **meta},
                    trace_id=action.trace_id,
                ),
            )
        )
        calendar_statuses = _action_statuses(actions, db_user.id, calendar_action_ids)
        results.append(
            StepResult(
                "calendar_action_worker",
                bool(calendar_outbox_ids)
                and all(status == ActionStatus.SUCCEEDED.value for status in calendar_statuses.values()),
                (
                    f"statuses={calendar_statuses or {}} "
                    f"queued_delivery_ids={','.join(map(str, calendar_outbox_ids)) or 'none'}"
                ),
            )
        )
        if args.send_telegram:
            for message in outbox.list_recent(limit=20):
                if message.id in calendar_outbox_ids:
                    results.append(_deliver_one(outbox, message, settings.bot_token, events=events))

    if args.voice_file:
        voice_path = Path(args.voice_file)
        voice_started = time.perf_counter()
        try:
            text = OpenAITranscriber(
                api_key=settings.openai_api_key,
                model=settings.openai_transcribe_model,
                base_url=settings.openai_base_url,
                timeout_seconds=settings.hermes_timeout_seconds,
            ).transcribe(voice_path.read_bytes(), filename=voice_path.name)
        except (OSError, TranscriptionError) as error:
            results.append(StepResult("voice_transcription", False, str(error), _elapsed_ms(voice_started)))
        else:
            reply = service.handle_text(args.tg_user_id, text)
            results.append(
                StepResult(
                    "voice_transcription",
                    reply.blocked_reason is None,
                    f"transcribed_chars={len(text)} intent={reply.intent.value}",
                    _elapsed_ms(voice_started),
                )
            )
    else:
        results.append(StepResult("voice_transcription", True, "skipped: pass --voice-file to test real audio"))

    _print_summary(results, started)
    return 0 if all(result.ok for result in results) else 1


@dataclass
class TimedValue:
    value: object
    elapsed_ms: int

    @property
    def ok(self) -> bool:
        return True


def _timed(_name: str, fn) -> TimedValue:
    started = time.perf_counter()
    return TimedValue(fn(), _elapsed_ms(started))


def _deliver_one(outbox, message, bot_token: str, *, events=None) -> StepResult:
    from assistant.delivery_outbox import classify_delivery_error

    started = time.perf_counter()
    try:
        _send_telegram_message(bot_token, message.chat_id, message.text, buttons=message.buttons)
    except Exception as error:
        classification = classify_delivery_error(error)
        if classification.retryable:
            retry_after = datetime.now(timezone.utc) + timedelta(seconds=classification.retry_after_seconds or 30)
            outbox.mark_retry(message.id, str(error), retry_after)
            return StepResult("telegram_delivery", False, f"retryable_error={error}", _elapsed_ms(started))
        outbox.mark_failed_permanent(message.id, str(error))
        return StepResult("telegram_delivery", False, f"permanent_error={error}", _elapsed_ms(started))
    outbox.mark_sent(message.id)
    if events is not None:
        events.log(
            message.user_id,
            "delivery_sent",
            {"delivery_id": message.id, "attempts": message.attempts},
            trace_id=message.trace_id,
        )
    return StepResult("telegram_delivery", True, f"sent_delivery_id={message.id}", _elapsed_ms(started))


def _send_telegram_message(
    bot_token: str,
    chat_id: int,
    text: str,
    *,
    buttons: list[list[dict[str, str]]] | None = None,
) -> None:
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {"chat_id": str(chat_id), "text": text[:3900]}
    if buttons:
        payload["reply_markup"] = json.dumps({"inline_keyboard": buttons}, ensure_ascii=False)
    data = urllib.parse.urlencode(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", "replace")
        retry_after = _retry_after_from_body(body)
        description = _description_from_body(body) or f"HTTP {error.code}"
        raise TelegramApiError(description, retry_after=retry_after) from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise TelegramApiError(str(error)) from error
    if not payload.get("ok"):
        description = str(payload.get("description") or "telegram returned ok=false")
        retry_after = None
        parameters = payload.get("parameters")
        if isinstance(parameters, dict) and isinstance(parameters.get("retry_after"), int):
            retry_after = parameters["retry_after"]
        raise TelegramApiError(description, retry_after=retry_after)


def _retry_after_from_body(body: str) -> int | None:
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return None
    parameters = data.get("parameters")
    if isinstance(parameters, dict) and isinstance(parameters.get("retry_after"), int):
        return parameters["retry_after"]
    return None


def _description_from_body(body: str) -> str | None:
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return None
    description = data.get("description")
    return str(description) if description else None


def _due_reminder_text(text: str) -> str:
    due_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    return f"/remind {due_at:%Y-%m-%d %H:%M} {text}"


def _compact(text: str, *, limit: int = 180) -> str:
    value = re.sub(r"\s+", " ", text or "").strip()
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


def _elapsed_ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)


def _action_ids(actions, user_id: int) -> set[int]:
    return {action.id for action in actions.list_for_user(user_id, limit=100)}


def _action_statuses(actions, user_id: int, ids: list[int]) -> dict[int, str]:
    wanted = set(ids)
    return {
        action.id: action.status.value
        for action in actions.list_for_user(user_id, limit=100)
        if action.id in wanted
    }


def _confirm_actions(service, tg_user_id: int, actions, user_id: int, ids: list[int]) -> StepResult:
    from assistant.action_queue import ActionStatus

    started = time.perf_counter()
    confirmed = []
    for action_id, status in _action_statuses(actions, user_id, ids).items():
        if status == ActionStatus.NEEDS_CONFIRMATION.value:
            reply = service.confirm_action(tg_user_id, action_id)
            if reply.blocked_reason:
                return StepResult("action_confirm", False, f"action_id={action_id} blocked={reply.blocked_reason}", _elapsed_ms(started))
            confirmed.append(action_id)
    statuses = _action_statuses(actions, user_id, ids)
    ok = all(status in {ActionStatus.QUEUED.value, ActionStatus.RUNNING.value, ActionStatus.SUCCEEDED.value} for status in statuses.values())
    return StepResult(
        "action_confirm",
        ok,
        f"confirmed={','.join(map(str, confirmed)) or 'none'} statuses={statuses} path=service_callback",
        _elapsed_ms(started),
    )


def _buttons_to_payload(buttons) -> list[list[dict[str, str]]]:
    return [[{"text": button.text, "callback_data": button.callback_data} for button in row] for row in buttons]


def _print_summary(results: list[StepResult], started: float) -> None:
    print("Live e2e summary")
    for result in results:
        status = "ok" if result.ok else "fail"
        suffix = f" {result.elapsed_ms}ms" if result.elapsed_ms else ""
        print(f"- {result.name}: {status}{suffix} — {result.detail}")
    print(json.dumps({"ok": all(item.ok for item in results), "elapsed_ms": _elapsed_ms(started), "steps": [asdict(item) for item in results]}, ensure_ascii=False))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a live-ish Telegram AI Brooch e2e scenario.")
    parser.add_argument("--tg-user-id", type=int, required=True, help="Telegram user/chat id that already started the bot.")
    parser.add_argument("--use-main-db", action="store_true", help="Use DATABASE_URL from .env instead of isolated data/live_e2e.sqlite3.")
    parser.add_argument("--keep-e2e-db", action="store_true", help="Keep the isolated e2e DB instead of recreating it.")
    parser.add_argument("--send-telegram", action="store_true", help="Send outbox messages through the real Telegram Bot API.")
    parser.add_argument("--require-real-llm", action="store_true", help="Fail when HERMES_MODE=fake.")
    parser.add_argument("--include-task", action="store_true", help="Create and execute a real queued task through Task Command Center.")
    parser.add_argument("--include-calendar", action="store_true", help="Create and execute a real queued Google Calendar event through Task Command Center.")
    parser.add_argument("--allow-doc-sync", action="store_true", help="Allow Google Docs/Sheets sync during the scenario.")
    parser.add_argument("--voice-file", help="Optional local audio file to test transcription path.")
    parser.add_argument("--ask-text", default="/ask ответь одним коротким предложением: live e2e ok?")
    parser.add_argument("--reminder-text", default="live e2e reminder")
    parser.add_argument("--task-text", default="создай задачу [live-e2e] проверить JarHert delivery")
    parser.add_argument("--calendar-text", default="поставь в календарь [live-e2e] JarHert check | start=2026-07-10 10:00 | end=2026-07-10 10:15")
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(main())
