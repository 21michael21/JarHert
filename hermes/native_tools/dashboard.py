"""Authenticated Telegram Mini App for the JarHert personal cabinet."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qsl

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from .mcp_api import NativeToolsAPI


ASSET_DIR = Path(__file__).with_name("dashboard_assets")
COOKIE_NAME = "jarhert_dashboard"
SESSION_SECONDS = 12 * 60 * 60
TELEGRAM_AUTH_MAX_AGE_SECONDS = 60 * 60
TELEGRAM_AUTH_FUTURE_SKEW_SECONDS = 5 * 60


@dataclass(frozen=True)
class DashboardSettings:
    """Runtime configuration. Secrets are read only from the profile environment."""

    bot_token: str
    session_secret: str
    allowed_user_ids: frozenset[int]
    secure_cookie: bool = True
    auth_max_age_seconds: int = TELEGRAM_AUTH_MAX_AGE_SECONDS

    @classmethod
    def from_env(cls) -> "DashboardSettings":
        bot_token = os.getenv("JARHERT_DASHBOARD_BOT_TOKEN", "").strip() or os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        session_secret = os.getenv("JARHERT_DASHBOARD_SESSION_SECRET", "").strip()
        raw_users = (
            os.getenv("JARHERT_DASHBOARD_ALLOWED_TG_USER_IDS", "").strip()
            or os.getenv("TELEGRAM_ALLOWED_USERS", "").strip()
        )
        allowed_user_ids = _parse_user_ids(raw_users)
        if not bot_token or not session_secret or not allowed_user_ids:
            raise RuntimeError(
                "JARHERT_DASHBOARD_SESSION_SECRET, TELEGRAM_BOT_TOKEN and "
                "JARHERT_DASHBOARD_ALLOWED_TG_USER_IDS (or TELEGRAM_ALLOWED_USERS) are required"
            )
        return cls(
            bot_token=bot_token,
            session_secret=session_secret,
            allowed_user_ids=allowed_user_ids,
            secure_cookie=os.getenv("JARHERT_DASHBOARD_SECURE_COOKIE", "true").strip().lower() != "false",
        )


def create_app(
    *,
    api: NativeToolsAPI | Any | None = None,
    settings: DashboardSettings | None = None,
    clock: Callable[[], float] = time.time,
) -> FastAPI:
    dashboard_api = api or NativeToolsAPI()
    config = settings or DashboardSettings.from_env()
    app = FastAPI(title="JarHert Cabinet", docs_url=None, redoc_url=None)

    @app.middleware("http")
    async def security_headers(request: Request, call_next: Callable[[Request], Any]) -> Any:
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self' https://telegram.org; style-src 'self'; "
            "connect-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'self'; "
            "frame-ancestors https://web.telegram.org https://*.telegram.org"
        )
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    def require_user(request: Request) -> int:
        user_id = _valid_session(request.cookies.get(COOKIE_NAME, ""), config.session_secret, clock=clock)
        if user_id is None or user_id not in config.allowed_user_ids:
            raise HTTPException(status_code=401, detail="telegram session required")
        return user_id

    @app.get("/", response_class=HTMLResponse)
    async def page() -> HTMLResponse:
        return HTMLResponse(_dashboard_page())

    @app.post("/api/session/telegram")
    async def telegram_session(request: Request) -> JSONResponse:
        payload = await _request_payload(request)
        init_data = str(payload.get("init_data") or "")
        user_id = _validate_telegram_init_data(init_data, config, clock=clock)
        if user_id not in config.allowed_user_ids:
            raise HTTPException(status_code=403, detail="this Telegram account is not allowed")
        response = JSONResponse({"ok": True, "user_id": user_id})
        response.set_cookie(
            COOKIE_NAME,
            _new_session(user_id, config.session_secret, clock=clock),
            max_age=SESSION_SECONDS,
            httponly=True,
            secure=config.secure_cookie,
            samesite="strict",
        )
        return response

    @app.post("/api/logout")
    async def logout() -> JSONResponse:
        response = JSONResponse({"ok": True})
        response.delete_cookie(COOKIE_NAME)
        return response

    @app.get("/api/snapshot")
    async def snapshot(request: Request) -> JSONResponse:
        require_user(request)
        return JSONResponse(_snapshot(dashboard_api))

    @app.get("/api/tasks")
    async def tasks(request: Request) -> JSONResponse:
        require_user(request)
        return JSONResponse(_call_read(dashboard_api.task_dashboard))

    @app.get("/api/calendar")
    async def calendar(request: Request, days: int = 7) -> JSONResponse:
        require_user(request)
        if not 1 <= days <= 31:
            raise HTTPException(status_code=422, detail="days must be between 1 and 31")
        return JSONResponse(_call_read(lambda: dashboard_api.calendar_dashboard(days=days)))

    @app.post("/api/plans")
    async def create_plan(request: Request) -> JSONResponse:
        user_id = require_user(request)
        payload = await _request_payload(request)
        request_id = str(payload.get("request_id") or "")
        actions = payload.get("actions")
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,80}", request_id) or not isinstance(actions, list):
            raise HTTPException(status_code=422, detail="invalid plan request")
        plan = _call_write(
            lambda: dashboard_api.action_plan_create(
                actions=actions,
                idempotency_key=f"dashboard:{user_id}:{request_id}",
            )
        )
        plan_id = _positive_id(plan["id"])
        return JSONResponse(
            {
                **plan,
                "plan_token": _new_plan_token(user_id, plan_id, config.session_secret),
                "preview": _plan_preview(plan),
            }
        )

    @app.post("/api/plans/{plan_id}/execute")
    async def execute_plan(plan_id: int, request: Request) -> JSONResponse:
        user_id = require_user(request)
        payload = await _request_payload(request)
        safe_plan_id = _positive_id(plan_id)
        _require_plan_token(payload.get("plan_token"), user_id, safe_plan_id, config.session_secret)
        return JSONResponse(_call_write(lambda: dashboard_api.action_plan_execute(plan_id=safe_plan_id, confirmed=True)))

    @app.post("/api/plans/{plan_id}/cancel")
    async def cancel_plan(plan_id: int, request: Request) -> JSONResponse:
        user_id = require_user(request)
        payload = await _request_payload(request)
        safe_plan_id = _positive_id(plan_id)
        _require_plan_token(payload.get("plan_token"), user_id, safe_plan_id, config.session_secret)
        return JSONResponse(_call_write(lambda: dashboard_api.action_plan_cancel(plan_id=safe_plan_id)))

    @app.post("/api/reminders/{reminder_id}/reschedule")
    async def reschedule_reminder(reminder_id: int, request: Request) -> JSONResponse:
        require_user(request)
        payload = await _request_payload(request)
        remind_at = _required_text(payload.get("remind_at"), label="Время напоминания", limit=80)
        recurrence = str(payload.get("recurrence") or "keep")
        if recurrence not in {"keep", "none", "daily", "weekly", "monthly"}:
            raise HTTPException(status_code=422, detail="unknown recurrence")
        return JSONResponse(
            _call_write(
                lambda: dashboard_api.reminder_reschedule(
                    reminder_id=_positive_id(reminder_id),
                    remind_at=remind_at,
                    recurrence=recurrence,
                )
            )
        )

    @app.post("/api/reminders/{reminder_id}/cancel")
    async def cancel_reminder(reminder_id: int, request: Request) -> JSONResponse:
        require_user(request)
        return JSONResponse(_call_write(lambda: dashboard_api.reminder_cancel(reminder_id=_positive_id(reminder_id))))

    @app.put("/api/notes/{note_id}")
    async def edit_note(note_id: int, request: Request) -> JSONResponse:
        require_user(request)
        payload = await _request_payload(request)
        content = _required_text(payload.get("content"), label="Текст заметки", limit=4000)
        return JSONResponse(_call_write(lambda: dashboard_api.note_edit(note_id=_positive_id(note_id), content=content)))

    @app.get("/assets/{asset_name}")
    async def asset(asset_name: str) -> FileResponse:
        allowed = {"dashboard.css", "dashboard.js"}
        if asset_name not in allowed:
            raise HTTPException(status_code=404, detail="asset not found")
        return FileResponse(ASSET_DIR / asset_name)

    return app


async def _request_payload(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception as error:
        raise HTTPException(status_code=400, detail="invalid request") from error
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="invalid request")
    return payload


def _snapshot(api: Any) -> dict[str, Any]:
    today = _safe(api.personal_today, fallback={})
    status = _safe(api.system_status, fallback={})
    reminders = _safe(lambda: api.reminder_list(status="active", limit=10), fallback={"items": []})
    notes = _safe(lambda: api.memory_block_list(block_type="note", limit=6), fallback={"items": []})
    monitors = _safe(api.monitor_list, fallback={"items": []})
    projects = _safe(api.project_context_list, fallback={"items": []})
    integrations = _safe(api.integration_health, fallback={})
    work_mode = _safe(api.work_mode_get, fallback={"mode": "fast"})
    tasks = _external_items(today.get("tasks"))
    priorities = list(today.get("top_three") or [])[:3]
    if not priorities:
        priorities = [{"title": task, "type": "task"} for task in tasks[:3]]
    return {
        "today": {
            "tasks": tasks,
            "calendar": _external_items(today.get("calendar")),
            "reminders": _items(reminders),
            "priorities": priorities,
        },
        "notes": _items(notes),
        "status": status,
        "integrations": integrations,
        "work_mode": work_mode,
        "monitors": _items(monitors),
        "projects": _items(projects),
        "capabilities": _capabilities(),
    }


def _safe(operation: Callable[[], Any], *, fallback: Any) -> Any:
    try:
        return operation()
    except Exception:
        return fallback


def _items(payload: Any) -> list[dict[str, Any]]:
    return [item for item in (payload or {}).get("items", []) if isinstance(item, dict)][:10]


def _external_items(value: Any) -> list[str]:
    raw = str(value or "").strip()
    if not raw or raw.casefold().rstrip(".!") in {"no events found", "событий нет"}:
        return []
    rows = [row.strip(" -•\t") for row in raw.splitlines() if row.strip()]
    return [row.split("|", 1)[0].replace("[open]", "").strip()[:160] for row in rows[:10]]


def _parse_user_ids(raw: str) -> frozenset[int]:
    values: set[int] = set()
    for item in raw.split(","):
        candidate = item.strip()
        if not candidate:
            continue
        if not candidate.isdigit() or int(candidate) <= 0:
            raise RuntimeError("Dashboard allowed Telegram user ids must be positive integers")
        values.add(int(candidate))
    return frozenset(values)


def _validate_telegram_init_data(init_data: str, settings: DashboardSettings, *, clock: Callable[[], float]) -> int:
    if not init_data or len(init_data) > 16_000:
        raise HTTPException(status_code=401, detail="invalid Telegram init data")
    pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=True)
    values: dict[str, str] = {}
    for key, value in pairs:
        if key in values:
            raise HTTPException(status_code=401, detail="invalid Telegram init data")
        values[key] = value
    received_hash = values.pop("hash", "")
    if not received_hash or not values:
        raise HTTPException(status_code=401, detail="invalid Telegram init data")
    data_check_string = "\n".join(f"{key}={values[key]}" for key in sorted(values))
    secret_key = hmac.new(b"WebAppData", settings.bot_token.encode("utf-8"), hashlib.sha256).digest()
    expected_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(received_hash, expected_hash):
        raise HTTPException(status_code=401, detail="invalid Telegram init data")
    try:
        auth_date = int(values["auth_date"])
        user = json.loads(values["user"])
        user_id = int(user["id"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise HTTPException(status_code=401, detail="invalid Telegram init data") from error
    age_seconds = int(clock()) - auth_date
    if age_seconds > settings.auth_max_age_seconds or age_seconds < -TELEGRAM_AUTH_FUTURE_SKEW_SECONDS:
        raise HTTPException(status_code=401, detail="expired Telegram init data")
    if user_id <= 0:
        raise HTTPException(status_code=401, detail="invalid Telegram init data")
    return user_id


def _new_session(user_id: int, secret: str, *, clock: Callable[[], float]) -> str:
    expires_at = str(int(clock()) + SESSION_SECONDS)
    payload = f"{expires_at}.{int(user_id)}"
    signature = hmac.new(secret.encode("utf-8"), payload.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}"


def _valid_session(token: str, secret: str, *, clock: Callable[[], float]) -> int | None:
    expires_at, user_id, signature = (token or "").split(".", 2) if (token or "").count(".") == 2 else ("", "", "")
    if not expires_at.isdigit() or not user_id.isdigit() or int(expires_at) < int(clock()):
        return None
    payload = f"{expires_at}.{user_id}"
    expected = hmac.new(secret.encode("utf-8"), payload.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None
    return int(user_id)


def _new_plan_token(user_id: int, plan_id: int, secret: str) -> str:
    payload = f"dashboard-plan.{int(user_id)}.{int(plan_id)}"
    signature = hmac.new(secret.encode("utf-8"), payload.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}"


def _require_plan_token(token: Any, user_id: int, plan_id: int, secret: str) -> None:
    expected = _new_plan_token(user_id, plan_id, secret)
    if not isinstance(token, str) or not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=403, detail="plan confirmation is not valid for this session")


def _positive_id(value: int) -> int:
    if int(value) <= 0:
        raise HTTPException(status_code=422, detail="invalid id")
    return int(value)


def _required_text(value: Any, *, label: str, limit: int) -> str:
    clean = str(value or "").strip()
    if not clean or len(clean) > limit:
        raise HTTPException(status_code=422, detail=f"{label} is invalid")
    return clean


def _call_write(operation: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        return operation()
    except (OSError, RuntimeError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)[:240]) from error


def _call_read(operation: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        return operation()
    except (OSError, RuntimeError, ValueError) as error:
        raise HTTPException(status_code=503, detail=str(error)[:240]) from error


def _plan_preview(plan: dict[str, Any]) -> list[str]:
    labels = {
        "task.create": "Создать задачу",
        "task.move": "Переместить задачу",
        "task.priority": "Изменить приоритет",
        "task.done": "Закрыть задачу",
        "task.delete": "Удалить задачу",
        "calendar.create": "Создать событие",
        "calendar.move": "Перенести событие",
        "calendar.delete": "Удалить событие",
        "reminder.create": "Создать напоминание",
        "note.save": "Сохранить заметку",
        "commitment.create": "Сохранить обещание",
    }
    rows: list[str] = []
    for action in list(plan.get("actions") or []):
        if not isinstance(action, dict):
            continue
        payload = action.get("payload") if isinstance(action.get("payload"), dict) else {}
        title = str(payload.get("title") or payload.get("text") or payload.get("subject") or "без названия")
        rows.append(f"{labels.get(str(action.get('action_type') or action.get('type') or ''), 'Выполнить')}: {title}")
    return rows


def _capabilities() -> list[dict[str, str]]:
    return [
        {"title": "План дня", "text": "Календарь, Trello, напоминания и три главных приоритета."},
        {"title": "Память", "text": "Заметки, проекты, люди, обещания и поиск по ним."},
        {"title": "Автоматизация", "text": "Напоминания, отложенные сообщения, сводки и monitors."},
        {"title": "Интеграции", "text": "Trello и Google Calendar через один подтверждённый план."},
        {"title": "Режимы", "text": "Быстро, думаю и код: с разными правами и лимитами."},
        {"title": "Безопасность", "text": "Код только в sandbox; важные действия подтверждаются в Telegram."},
    ]


def _dashboard_page() -> str:
    return """<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>JarHert</title><link rel="stylesheet" href="/assets/dashboard.css"><script src="https://telegram.org/js/telegram-web-app.js?62"></script></head>
<body>
<main class="shell">
  <header class="topbar"><div><p class="eyebrow">PERSONAL OS</p><h1>JarHert</h1></div><div class="top-actions"><span id="mode-chip" class="chip">Быстро</span><button id="refresh" class="icon-button" type="button" aria-label="Обновить">↻</button></div></header>
  <section id="loading-panel" class="loading-panel"><span class="loading-mark"></span><p id="loading-text">Открываю кабинет</p></section>
  <section id="cabinet" hidden>
    <div id="notice" class="notice" hidden></div>
    <nav class="view-tabs" aria-label="Разделы"><button class="view-tab is-active" data-view="today" type="button">Сегодня</button><button class="view-tab" data-view="tasks" type="button">Задачи</button><button class="view-tab" data-view="calendar" type="button">Календарь</button><button class="view-tab" data-view="inbox" type="button">Входящее</button></nav>
    <section id="view-today" class="view"><article class="focus-card"><p class="eyebrow">СЕЙЧАС</p><h2 id="focus-title">Собираю твой день</h2><p id="focus-meta" class="muted">Задача появится здесь.</p><div class="focus-actions"><button id="focus-done" class="primary" type="button">Сделано</button><button id="focus-move" class="secondary" type="button">Перенести</button></div></article><section class="section"><div class="section-head"><div><p class="eyebrow">ДЕНЬ</p><h2>Следом</h2></div><button class="text-button" data-open-view="calendar" type="button">Весь календарь</button></div><div id="today-calendar" class="timeline"></div></section><section class="section"><div class="section-head"><div><p class="eyebrow">ОЧЕРЕДЬ</p><h2>Три главных</h2></div><button class="text-button" data-open-view="tasks" type="button">Все задачи</button></div><div id="priorities" class="work-list"></div></section></section>
    <section id="view-tasks" class="view" hidden><div class="section-head"><div><p class="eyebrow">TRELLO</p><h2>Задачи</h2></div><button id="open-trello" class="text-button" type="button">Открыть Trello</button></div><div id="task-filters" class="filter-row"></div><div id="task-list" class="work-list"></div></section>
    <section id="view-calendar" class="view" hidden><div class="section-head"><div><p class="eyebrow">7 ДНЕЙ</p><h2>Календарь</h2></div><button id="open-calendar" class="text-button" type="button">Google Calendar</button></div><div id="calendar-list" class="timeline"></div></section>
    <section id="view-inbox" class="view" hidden><section class="section"><div class="section-head"><div><p class="eyebrow">НАПОМИНАНИЯ</p><h2>Ближайшее</h2></div><span id="reminder-count" class="count-pill">0</span></div><div id="reminders" class="work-list"></div></section><section class="section"><div class="section-head"><div><p class="eyebrow">ЗАМЕТКИ</p><h2>Последние</h2></div></div><div id="notes" class="work-list"></div></section><section class="section status-section"><div class="section-head"><div><p class="eyebrow">СИСТЕМА</p><h2>Статус</h2></div></div><div id="system" class="status-list"></div></section></section>
  </section>
</main>
<button id="quick-add" class="quick-add" type="button" aria-label="Добавить">＋ <span>Добавить</span></button>
<nav id="bottom-nav" class="bottom-nav" aria-label="Навигация"><button class="nav-button is-active" data-view="today" type="button">Сегодня</button><button class="nav-button" data-view="tasks" type="button">Задачи</button><button class="nav-button" data-view="calendar" type="button">План</button><button class="nav-button" data-view="inbox" type="button">Входящее</button></nav>
<dialog id="quick-dialog"><form id="quick-form"><p class="eyebrow">БЫСТРОЕ ДОБАВЛЕНИЕ</p><h2 id="quick-title">Новая задача</h2><div class="quick-types"><button class="type-button is-active" data-quick-type="task" type="button">Задача</button><button class="type-button" data-quick-type="event" type="button">Встреча</button><button class="type-button" data-quick-type="reminder" type="button">Напоминание</button><button class="type-button" data-quick-type="note" type="button">Заметка</button></div><label><span id="quick-label">Что сделать</span><textarea id="quick-text" rows="3" maxlength="1000" required></textarea></label><label id="quick-list-field"><span>Колонка</span><select id="quick-list"></select></label><label id="quick-priority-field"><span>Приоритет</span><select id="quick-priority"></select></label><label id="quick-start-field" hidden><span id="quick-start-label">Когда</span><input id="quick-start" type="datetime-local"></label><label id="quick-end-field" hidden><span>До</span><input id="quick-end" type="datetime-local"></label><div class="dialog-actions"><button id="quick-cancel" class="secondary" type="button">Отмена</button><button class="primary" type="submit">К preview</button></div></form></dialog>
<dialog id="edit-dialog"><form id="edit-form"><p class="eyebrow" id="edit-eyebrow">КОРРЕКТИРОВКА</p><h2 id="edit-title">Изменить</h2><p id="edit-help" class="muted"></p><label id="edit-field-label"><span id="edit-field-name">Значение</span><input id="edit-date" type="datetime-local" hidden><input id="edit-end" type="datetime-local" hidden><select id="edit-choice" hidden></select><textarea id="edit-value" rows="5"></textarea></label><label id="recurrence-field" hidden><span>Повтор</span><select id="edit-recurrence"><option value="keep">Не менять</option><option value="none">Не повторять</option><option value="daily">Каждый день</option><option value="weekly">Каждую неделю</option><option value="monthly">Каждый месяц</option></select></label><div class="dialog-actions"><button id="dialog-cancel" class="secondary" type="button">Отмена</button><button id="dialog-save" class="primary" type="submit">К preview</button></div></form></dialog>
<dialog id="plan-dialog"><form id="plan-form"><p class="eyebrow">ПРОВЕРЬ И ПОДТВЕРДИ</p><h2>План действий</h2><div id="plan-preview" class="preview-list"></div><p class="muted">Изменения применятся один раз после подтверждения.</p><div class="dialog-actions"><button id="plan-cancel" class="secondary" type="button">Отмена</button><button id="plan-execute" class="primary" type="submit">Применить</button></div></form></dialog>
<script src="/assets/dashboard.js" defer></script></body></html>"""


app = create_app() if os.getenv("JARHERT_DASHBOARD_AUTOSTART") == "1" else None
