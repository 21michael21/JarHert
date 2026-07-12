"""Authenticated Telegram Mini App for the JarHert personal cabinet."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
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
  <header class="topbar"><div><p class="eyebrow">PERSONAL OS</p><h1>JarHert</h1></div><div class="top-actions"><span id="mode-chip" class="chip">Загрузка</span><button id="refresh" class="icon-button" type="button" aria-label="Обновить">↻</button></div></header>
  <section id="loading-panel" class="login-panel"><p class="eyebrow">TELEGRAM MINI APP</p><h2>Открываю кабинет</h2><p id="loading-text">Проверяю Telegram-сессию.</p></section>
  <section id="cabinet" hidden>
    <div id="notice" class="notice" hidden></div>
    <section class="summary-grid"><article class="summary-card"><span>Трелло</span><strong id="task-count">—</strong><small>задач на сегодня</small></article><article class="summary-card"><span>Календарь</span><strong id="calendar-count">—</strong><small>встреч на сегодня</small></article><article class="summary-card"><span>Напоминания</span><strong id="reminder-count">—</strong><small>активных</small></article><article class="summary-card"><span>Monitors</span><strong id="monitor-count">—</strong><small>включено</small></article></section>
    <section class="grid primary-grid"><article class="panel"><div class="panel-head"><div><p class="eyebrow">СЕГОДНЯ</p><h2>Главное</h2></div></div><div id="priorities" class="list"></div></article><article class="panel"><p class="eyebrow">КАЛЕНДАРЬ</p><h2>Расписание</h2><div id="calendar" class="list"></div></article><article class="panel"><p class="eyebrow">TRELLO</p><h2>Очередь</h2><div id="tasks" class="list"></div></article></section>
    <section class="grid secondary-grid"><article class="panel"><p class="eyebrow">НАПОМИНАНИЯ</p><h2>Ближайшее</h2><div id="reminders" class="list"></div></article><article class="panel"><p class="eyebrow">ЗАМЕТКИ</p><h2>Последние</h2><div id="notes" class="list"></div></article><article class="panel"><p class="eyebrow">СОСТОЯНИЕ</p><h2>Система</h2><div id="system" class="status-list"></div></article></section>
    <section class="grid tertiary-grid"><article class="panel"><p class="eyebrow">ПРОЕКТЫ</p><h2>Контексты</h2><div id="projects" class="list"></div></article><article class="panel capabilities"><p class="eyebrow">ВОЗМОЖНОСТИ</p><h2>Что можно поручить</h2><div id="capabilities" class="capability-grid"></div></article></section>
  </section>
</main>
<dialog id="edit-dialog"><form id="edit-form" method="dialog"><p class="eyebrow" id="edit-eyebrow">КОРРЕКТИРОВКА</p><h2 id="edit-title">Изменить</h2><p id="edit-help" class="muted"></p><label id="edit-field-label"><span id="edit-field-name">Значение</span><input id="edit-date" type="datetime-local" hidden><textarea id="edit-value" rows="5"></textarea></label><label id="recurrence-field" hidden><span>Повтор</span><select id="edit-recurrence"><option value="keep">Не менять</option><option value="none">Не повторять</option><option value="daily">Каждый день</option><option value="weekly">Каждую неделю</option><option value="monthly">Каждый месяц</option></select></label><div class="dialog-actions"><button id="dialog-cancel" class="secondary" value="cancel" type="button">Отмена</button><button id="dialog-save" class="primary" value="default" type="submit">Сохранить</button></div></form></dialog>
<script src="/assets/dashboard.js" defer></script></body></html>"""


app = create_app() if os.getenv("JARHERT_DASHBOARD_AUTOSTART") == "1" else None
