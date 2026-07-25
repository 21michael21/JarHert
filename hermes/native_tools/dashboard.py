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

from .dashboard_action_plans import coding_context, coding_mode, coding_preview, plan_preview
from .knowledge_archive import validate_archive_url
from .mcp_api import NativeToolsAPI
from .dashboard_read_model import build_dashboard_snapshot


ASSET_DIR = Path(__file__).with_name("dashboard_assets")
COOKIE_NAME = "jarhert_dashboard"
SESSION_SECONDS = 12 * 60 * 60
TELEGRAM_AUTH_MAX_AGE_SECONDS = 60 * 60
TELEGRAM_AUTH_FUTURE_SKEW_SECONDS = 5 * 60
CLIP_TOKEN_SECONDS = 15 * 60
CODING_TOKEN_SECONDS = 15 * 60


def _asset_version() -> str:
    digest = hashlib.sha256()
    for asset_name in ("dashboard.css", "dashboard.js"):
        digest.update((ASSET_DIR / asset_name).read_bytes())
    return digest.hexdigest()[:12]


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
        return JSONResponse(build_dashboard_snapshot(dashboard_api))

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

    @app.get("/api/coding/jobs")
    async def coding_jobs(request: Request) -> JSONResponse:
        require_user(request)
        return JSONResponse(_call_read(lambda: dashboard_api.coding_job_list(limit=20, include_result=True)))

    @app.post("/api/coding/jobs/preview")
    async def preview_coding_job(request: Request) -> JSONResponse:
        user_id = require_user(request)
        payload = await _request_payload(request)
        request_id = _request_id(payload.get("request_id"))
        prompt = _required_text(payload.get("prompt"), label="Кодовая задача", limit=3_000)
        mode = coding_mode(payload.get("mode"))
        repository_url, source_urls = coding_context(payload, mode=mode)
        return JSONResponse(
            {
                "request_id": request_id,
                "mode": mode,
                "prompt": prompt,
                "repository_url": repository_url,
                "source_urls": source_urls,
                "preview": coding_preview(mode=mode, repository_url=repository_url, source_urls=source_urls),
                "coding_token": _new_coding_token(
                    user_id=user_id,
                    request_id=request_id,
                    mode=mode,
                    prompt=prompt,
                    repository_url=repository_url,
                    source_urls=source_urls,
                    secret=config.session_secret,
                    clock=clock,
                ),
            }
        )

    @app.post("/api/coding/jobs/execute")
    async def execute_coding_job(request: Request) -> JSONResponse:
        user_id = require_user(request)
        payload = await _request_payload(request)
        request_id = _request_id(payload.get("request_id"))
        prompt = _required_text(payload.get("prompt"), label="Кодовая задача", limit=3_000)
        mode = coding_mode(payload.get("mode"))
        repository_url, source_urls = coding_context(payload, mode=mode)
        _require_coding_token(
            payload.get("coding_token"),
            user_id=user_id,
            request_id=request_id,
            mode=mode,
            prompt=prompt,
            repository_url=repository_url,
            source_urls=source_urls,
            secret=config.session_secret,
            clock=clock,
        )
        return JSONResponse(
            _call_write(
                lambda: dashboard_api.coding_job_enqueue(
                    mode=mode,
                    prompt=prompt,
                    repository_url=repository_url,
                    source_urls=source_urls,
                    idempotency_key=f"dashboard:coding:{user_id}:{request_id}",
                )
            )
        )

    @app.get("/api/notes")
    async def notes(request: Request, query: str = "", project: str | None = None) -> JSONResponse:
        require_user(request)
        clean_query = query.strip()
        if len(clean_query) > 200 or project is not None and len(project.strip()) > 120:
            raise HTTPException(status_code=422, detail="note query is invalid")
        if clean_query:
            return JSONResponse(
                _call_read(lambda: dashboard_api.note_search(query=clean_query, project=project, limit=50))
            )
        return JSONResponse(
            _call_read(lambda: dashboard_api.memory_block_list(block_type="note", project=project, limit=50))
        )

    @app.get("/api/notes/{note_id}/history")
    async def note_history(note_id: int, request: Request) -> JSONResponse:
        require_user(request)
        return JSONResponse(_call_read(lambda: dashboard_api.note_history(note_id=_positive_id(note_id))))

    @app.get("/api/knowledge/sources")
    async def knowledge_sources(request: Request, project: str | None = None) -> JSONResponse:
        require_user(request)
        if project is not None and len(project.strip()) > 120:
            raise HTTPException(status_code=422, detail="knowledge project is invalid")
        return JSONResponse(
            _call_read(lambda: dashboard_api.knowledge_list_sources(project=project, limit=50))
        )

    @app.get("/api/subscriptions")
    async def subscriptions(request: Request) -> JSONResponse:
        require_user(request)
        return JSONResponse(_call_read(lambda: dashboard_api.subscription_list(status="active")))

    @app.get("/api/monitors/digest")
    async def monitor_digest(request: Request) -> JSONResponse:
        require_user(request)
        return JSONResponse(_call_read(dashboard_api.monitor_digest))

    @app.put("/api/monitors/{monitor_id}/schedule")
    async def update_monitor_schedule(monitor_id: int, request: Request) -> JSONResponse:
        require_user(request)
        payload = await _request_payload(request)
        quiet_hours = str(payload.get("quiet_hours") or "").strip() or None
        if quiet_hours is not None and len(quiet_hours) > 20:
            raise HTTPException(status_code=422, detail="quiet hours are invalid")
        timezone_name = str(payload.get("timezone") or "Europe/Moscow").strip()
        if not timezone_name or len(timezone_name) > 80:
            raise HTTPException(status_code=422, detail="timezone is invalid")
        return JSONResponse(
            _call_write(
                lambda: dashboard_api.monitor_schedule_update(
                    monitor_id=_positive_id(monitor_id),
                    quiet_hours=quiet_hours,
                    timezone_name=timezone_name,
                )
            )
        )

    @app.post("/api/knowledge/clips/preview")
    async def preview_knowledge_clip(request: Request) -> JSONResponse:
        user_id = require_user(request)
        payload = await _request_payload(request)
        request_id = _request_id(payload.get("request_id"))
        url = _call_write(lambda: {"url": validate_archive_url(str(payload.get("url") or ""))})["url"]
        project = _optional_text(payload.get("project"), limit=120)
        return JSONResponse(
            {
                "url": url,
                "project": project,
                "request_id": request_id,
                "preview": ["Сохранить страницу в базу знаний", *([f"Проект: {project}"] if project else [])],
                "clip_token": _new_clip_token(
                    user_id=user_id,
                    request_id=request_id,
                    url=url,
                    project=project,
                    secret=config.session_secret,
                    clock=clock,
                ),
            }
        )

    @app.post("/api/knowledge/clips/execute")
    async def execute_knowledge_clip(request: Request) -> JSONResponse:
        user_id = require_user(request)
        payload = await _request_payload(request)
        request_id = _request_id(payload.get("request_id"))
        url = _call_write(lambda: {"url": validate_archive_url(str(payload.get("url") or ""))})["url"]
        project = _optional_text(payload.get("project"), limit=120)
        _require_clip_token(
            payload.get("clip_token"),
            user_id=user_id,
            request_id=request_id,
            url=url,
            project=project,
            secret=config.session_secret,
            clock=clock,
        )
        return JSONResponse(_call_write(lambda: dashboard_api.knowledge_archive_url(url=url, project=project)))

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
                "preview": plan_preview(plan),
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

    @app.delete("/api/notes/{note_id}")
    async def delete_note(note_id: int, request: Request) -> JSONResponse:
        require_user(request)
        return JSONResponse(_call_write(lambda: dashboard_api.note_delete(note_id=_positive_id(note_id))))

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


def _new_clip_token(
    *,
    user_id: int,
    request_id: str,
    url: str,
    project: str | None,
    secret: str,
    clock: Callable[[], float],
) -> str:
    expires_at = int(clock()) + CLIP_TOKEN_SECONDS
    digest = hashlib.sha256(f"{url}\n{project or ''}".encode("utf-8")).hexdigest()
    payload = f"dashboard-clip.{int(user_id)}.{expires_at}.{request_id}.{digest}"
    signature = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}"


def _require_clip_token(
    token: Any,
    *,
    user_id: int,
    request_id: str,
    url: str,
    project: str | None,
    secret: str,
    clock: Callable[[], float],
) -> None:
    if not isinstance(token, str):
        raise HTTPException(status_code=403, detail="knowledge clip confirmation is required")
    parts = token.split(".")
    if len(parts) != 6 or parts[0] != "dashboard-clip" or not parts[1].isdigit() or not parts[2].isdigit():
        raise HTTPException(status_code=403, detail="knowledge clip confirmation is not valid")
    token_user_id = int(parts[1]) if parts[1].isdigit() else -1
    if token_user_id != int(user_id) or int(parts[2]) < int(clock()) or parts[3] != request_id:
        raise HTTPException(status_code=403, detail="knowledge clip confirmation is not valid")
    expected = _new_clip_token(
        user_id=user_id,
        request_id=request_id,
        url=url,
        project=project,
        secret=secret,
        clock=lambda: int(parts[2]) - CLIP_TOKEN_SECONDS,
    )
    if not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=403, detail="knowledge clip confirmation is not valid")


def _new_coding_token(
    *,
    user_id: int,
    request_id: str,
    mode: str,
    prompt: str,
    repository_url: str | None,
    source_urls: list[str],
    secret: str,
    clock: Callable[[], float],
) -> str:
    expires_at = int(clock()) + CODING_TOKEN_SECONDS
    digest = hashlib.sha256(
        json.dumps(
            {
                "mode": mode,
                "prompt": prompt,
                "repository_url": repository_url,
                "source_urls": source_urls,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    payload = f"dashboard-coding.{int(user_id)}.{expires_at}.{request_id}.{digest}"
    signature = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}"


def _require_coding_token(
    token: Any,
    *,
    user_id: int,
    request_id: str,
    mode: str,
    prompt: str,
    repository_url: str | None,
    source_urls: list[str],
    secret: str,
    clock: Callable[[], float],
) -> None:
    if not isinstance(token, str):
        raise HTTPException(status_code=403, detail="coding job confirmation is required")
    parts = token.split(".")
    if len(parts) != 6 or parts[0] != "dashboard-coding" or not parts[1].isdigit() or not parts[2].isdigit():
        raise HTTPException(status_code=403, detail="coding job confirmation is not valid")
    if int(parts[1]) != int(user_id) or int(parts[2]) < int(clock()) or parts[3] != request_id:
        raise HTTPException(status_code=403, detail="coding job confirmation is not valid")
    expected = _new_coding_token(
        user_id=user_id,
        request_id=request_id,
        mode=mode,
        prompt=prompt,
        repository_url=repository_url,
        source_urls=source_urls,
        secret=secret,
        clock=lambda: int(parts[2]) - CODING_TOKEN_SECONDS,
    )
    if not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=403, detail="coding job confirmation is not valid")


def _positive_id(value: int) -> int:
    if int(value) <= 0:
        raise HTTPException(status_code=422, detail="invalid id")
    return int(value)


def _required_text(value: Any, *, label: str, limit: int) -> str:
    clean = str(value or "").strip()
    if not clean or len(clean) > limit:
        raise HTTPException(status_code=422, detail=f"{label} is invalid")
    return clean


def _optional_text(value: Any, *, limit: int) -> str | None:
    clean = str(value or "").strip()
    if len(clean) > limit:
        raise HTTPException(status_code=422, detail="text is invalid")
    return clean or None


def _request_id(value: Any) -> str:
    clean = str(value or "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,80}", clean):
        raise HTTPException(status_code=422, detail="invalid request id")
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


def _dashboard_page() -> str:
    return """<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover"><meta name="theme-color" content="#030814"><meta name="color-scheme" content="dark">
<title>JarHert</title><link rel="stylesheet" href="/assets/dashboard.css?v=__ASSET_VERSION__"><script src="https://telegram.org/js/telegram-web-app.js?62"></script></head>
<body>
<svg class="icon-sprite" aria-hidden="true" focusable="false">
  <symbol id="icon-sparkles" viewBox="0 0 24 24"><path d="m12 3-1.9 5.8a2 2 0 0 1-1.3 1.3L3 12l5.8 1.9a2 2 0 0 1 1.3 1.3L12 21l1.9-5.8a2 2 0 0 1 1.3-1.3L21 12l-5.8-1.9a2 2 0 0 1-1.3-1.3Z"/><path d="M5 3v4M19 17v4M3 5h4M17 19h4"/></symbol>
  <symbol id="icon-refresh-cw" viewBox="0 0 24 24"><path d="M21 12a9 9 0 0 0-15-6.7L3 8"/><path d="M3 3v5h5M3 12a9 9 0 0 0 15 6.7l3-2.7"/><path d="M21 21v-5h-5"/></symbol>
  <symbol id="icon-list-todo" viewBox="0 0 24 24"><rect width="6" height="6" x="3" y="5" rx="1"/><path d="m3 17 2 2 4-4M13 6h8M13 12h8M13 18h8"/></symbol>
  <symbol id="icon-calendar-days" viewBox="0 0 24 24"><path d="M8 2v4M16 2v4"/><rect width="18" height="18" x="3" y="4" rx="2"/><path d="M3 10h18M8 14h.01M12 14h.01M16 14h.01M8 18h.01M12 18h.01"/></symbol>
  <symbol id="icon-radar" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2"/><path d="M12 2v2M22 12h-2M12 22v-2M2 12h2"/></symbol>
  <symbol id="icon-route" viewBox="0 0 24 24"><circle cx="6" cy="19" r="3"/><circle cx="18" cy="5" r="3"/><path d="M9 19h5.5a3.5 3.5 0 0 0 0-7h-5a3.5 3.5 0 0 1 0-7H15"/></symbol>
  <symbol id="icon-code-2" viewBox="0 0 24 24"><path d="m18 16 4-4-4-4M6 8l-4 4 4 4M14.5 4l-5 16"/></symbol>
  <symbol id="icon-database" viewBox="0 0 24 24"><ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5v14a9 3 0 0 0 18 0V5M3 12a9 3 0 0 0 18 0"/></symbol>
  <symbol id="icon-plus" viewBox="0 0 24 24"><path d="M5 12h14M12 5v14"/></symbol>
  <symbol id="icon-check" viewBox="0 0 24 24"><path d="m20 6-11 11-5-5"/></symbol>
  <symbol id="icon-chevron-right" viewBox="0 0 24 24"><path d="m9 18 6-6-6-6"/></symbol>
  <symbol id="icon-external-link" viewBox="0 0 24 24"><path d="M15 3h6v6M10 14 21 3M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/></symbol>
  <symbol id="icon-search" viewBox="0 0 24 24"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/></symbol>
  <symbol id="icon-bell" viewBox="0 0 24 24"><path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4"/></symbol>
  <symbol id="icon-sticky-note" viewBox="0 0 24 24"><path d="M16 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V8Z"/><path d="M16 3v5h5M8 13h8M8 17h5"/></symbol>
  <symbol id="icon-book-open" viewBox="0 0 24 24"><path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2Z"/><path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7Z"/></symbol>
  <symbol id="icon-clock" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></symbol>
  <symbol id="icon-more-horizontal" viewBox="0 0 24 24"><circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/></symbol>
  <symbol id="icon-pencil" viewBox="0 0 24 24"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"/></symbol>
  <symbol id="icon-trash-2" viewBox="0 0 24 24"><path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6M10 11v5M14 11v5"/></symbol>
  <symbol id="icon-history" viewBox="0 0 24 24"><path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5M12 7v5l3 2"/></symbol>
  <symbol id="icon-file-text" viewBox="0 0 24 24"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z"/><path d="M14 2v6h6M8 13h8M8 17h8M8 9h2"/></symbol>
  <symbol id="icon-github" viewBox="0 0 24 24"><path d="M15 22v-4a4.8 4.8 0 0 0-1-3.5c3.3-.4 6.7-1.6 6.7-7A5.4 5.4 0 0 0 19.3 4 5 5 0 0 0 19.2.5S18.1.1 15 1.8a13.4 13.4 0 0 0-7 0C4.9.1 3.8.5 3.8.5A5 5 0 0 0 3.7 4a5.4 5.4 0 0 0-1.4 3.7c0 5.4 3.4 6.6 6.7 7A4.8 4.8 0 0 0 8 18v4"/><path d="M8 19c-3 .9-3-1.5-4-2"/></symbol>
  <symbol id="icon-radio" viewBox="0 0 24 24"><path d="M4.9 19.1a10 10 0 0 1 0-14.2M7.8 16.2a6 6 0 0 1 0-8.5"/><circle cx="12" cy="12" r="2"/><path d="M16.2 7.8a6 6 0 0 1 0 8.5M19.1 4.9a10 10 0 0 1 0 14.2"/></symbol>
  <symbol id="icon-terminal-square" viewBox="0 0 24 24"><rect width="18" height="18" x="3" y="3" rx="2"/><path d="m7 8 3 3-3 3M13 16h4"/></symbol>
  <symbol id="icon-archive" viewBox="0 0 24 24"><path d="M3 5h18v4H3zM5 9v11h14V9M10 13h4"/></symbol>
  <symbol id="icon-moon" viewBox="0 0 24 24"><path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/></symbol>
  <symbol id="icon-mic" viewBox="0 0 24 24"><rect width="6" height="12" x="9" y="2" rx="3"/><path d="M5 10a7 7 0 0 0 14 0M12 19v3"/></symbol>
  <symbol id="icon-undo" viewBox="0 0 24 24"><path d="M3 7v6h6"/><path d="M21 17a9 9 0 0 0-15-6.7L3 13"/></symbol>
  <symbol id="icon-flame" viewBox="0 0 24 24"><path d="M8.5 14.5A2.5 2.5 0 0 0 11 12c0-1.38-.5-2-1-3 1.07-2.14 2.5-3.5 4.5-4 0 1.5.5 3 2 4.5S19 12.5 19 15a7 7 0 1 1-14 0c0-1.15.43-2.29 1-3a2.5 2.5 0 0 0 2.5 2.5Z"/></symbol>
</svg>
<div id="pull-indicator" class="pull-indicator" aria-hidden="true"><span class="pull-spinner"></span></div>
<div id="toast" class="toast" role="status" aria-live="polite" hidden><span id="toast-text"></span><button id="toast-action" class="toast-action" type="button" hidden></button></div>
<main class="shell">
  <header class="topbar">
    <div class="brand-lockup"><p class="eyebrow">PERSONAL OS</p><h1>JarHert</h1><p id="last-sync" class="header-status">Соединяюсь с твоим контуром</p></div>
    <div class="top-actions"><span id="mode-chip" class="chip"><svg class="icon icon-warm" aria-hidden="true"><use href="#icon-sparkles"></use></svg><span id="mode-chip-label">Быстро</span></span><button id="refresh" class="icon-button" type="button" aria-label="Обновить данные"><svg class="icon" aria-hidden="true"><use href="#icon-refresh-cw"></use></svg></button></div>
  </header>
  <section id="loading-panel" class="loading-panel" aria-live="polite"><span class="loading-mark" aria-hidden="true"></span><p id="loading-text">Открываю кабинет</p><div class="loading-skeletons" aria-hidden="true"><span></span><span></span><span></span></div></section>
  <section id="cabinet" hidden>
    <div id="notice" class="notice" role="status" aria-live="polite" hidden></div>
    <nav class="view-tabs" aria-label="Разделы">
      <button class="view-tab is-active" data-view="today" type="button" aria-current="page"><svg class="icon icon-sm" aria-hidden="true"><use href="#icon-sparkles"></use></svg>Сегодня</button><button class="view-tab" data-view="tasks" type="button"><svg class="icon icon-sm" aria-hidden="true"><use href="#icon-list-todo"></use></svg>Задачи</button><button class="view-tab" data-view="calendar" type="button"><svg class="icon icon-sm" aria-hidden="true"><use href="#icon-calendar-days"></use></svg>Календарь</button><button class="view-tab" data-view="code" type="button"><svg class="icon icon-sm" aria-hidden="true"><use href="#icon-code-2"></use></svg>Код</button><button class="view-tab" data-view="memory" type="button"><svg class="icon icon-sm" aria-hidden="true"><use href="#icon-database"></use></svg>Память</button>
    </nav>
    <section id="view-today" class="view">
      <section class="overview-grid" aria-label="Сводка">
        <button id="overview-tasks" class="overview-tile" data-open-view="tasks" type="button"><span class="overview-heading"><svg class="icon" aria-hidden="true"><use href="#icon-list-todo"></use></svg><span class="overview-label">Задачи</span></span><strong id="overview-tasks-value">0</strong><span id="overview-tasks-meta" class="overview-meta">на сегодня</span><svg class="sparkline" id="spark-tasks" viewBox="0 0 64 20" preserveAspectRatio="none" aria-hidden="true"></svg></button>
        <button id="overview-calendar" class="overview-tile" data-open-view="calendar" type="button"><span class="overview-heading"><svg class="icon" aria-hidden="true"><use href="#icon-calendar-days"></use></svg><span class="overview-label">Календарь</span></span><strong id="overview-calendar-value">0</strong><span id="overview-calendar-meta" class="overview-meta">ближайшие 7 дней</span><svg class="sparkline" id="spark-calendar" viewBox="0 0 64 20" preserveAspectRatio="none" aria-hidden="true"></svg></button>
        <button id="overview-radar" class="overview-tile" data-open-view="memory" type="button"><span class="overview-heading"><svg class="icon" aria-hidden="true"><use href="#icon-radar"></use></svg><span class="overview-label">Радар</span></span><strong id="overview-radar-value">0</strong><span id="overview-radar-meta" class="overview-meta">без новых сигналов</span><svg class="sparkline" id="spark-radar" viewBox="0 0 64 20" preserveAspectRatio="none" aria-hidden="true"></svg></button>
      </section>
      <button id="architecture-open-home" class="architecture-teaser" type="button" aria-haspopup="dialog"><span class="architecture-teaser-icon"><svg class="icon icon-lg" aria-hidden="true"><use href="#icon-route"></use></svg></span><span><span class="eyebrow">КАК ЭТО РАБОТАЕТ</span><strong>Проследи путь запроса</strong><small>Выбери сценарий и посмотри живой маршрут до результата.</small></span><span class="architecture-teaser-action">Карта<svg class="icon icon-sm" aria-hidden="true"><use href="#icon-chevron-right"></use></svg></span></button>
      <article class="focus-card"><div class="focus-topline"><p class="eyebrow">ГЛАВНОЕ СЕЙЧАС</p><span id="focus-state" class="state-dot">Фокус</span></div><div class="focus-body"><div class="focus-copy"><h2 id="focus-title">Собираю твой день</h2><p id="focus-meta" class="muted">Задача появится здесь.</p><p id="momentum" class="momentum" hidden></p></div><div class="focus-ring" id="focus-ring" role="img" aria-label="Прогресс дня"><svg viewBox="0 0 72 72" aria-hidden="true"><circle class="ring-track" cx="36" cy="36" r="31"></circle><circle class="ring-progress" id="ring-progress" cx="36" cy="36" r="31"></circle></svg><div class="ring-center"><strong id="ring-done">0</strong><span id="ring-total">из 0</span></div></div></div><div class="focus-actions"><button id="focus-done" class="primary" type="button"><svg class="icon" aria-hidden="true"><use href="#icon-check"></use></svg>Отметить готовой</button><button id="focus-move" class="secondary" type="button"><svg class="icon" aria-hidden="true"><use href="#icon-clock"></use></svg>Перенести</button></div></article>
      <section class="section"><div class="section-head"><div><p class="eyebrow">ПО РАСПИСАНИЮ</p><h2>Следом</h2></div><button class="text-button" data-open-view="calendar" type="button">Все встречи<svg class="icon icon-sm" aria-hidden="true"><use href="#icon-chevron-right"></use></svg></button></div><div id="today-calendar" class="timeline"></div></section>
      <section class="section"><div class="section-head"><div><p class="eyebrow">ОЧЕРЕДЬ</p><h2>Три главных</h2></div><button class="text-button" data-open-view="tasks" type="button">Все задачи<svg class="icon icon-sm" aria-hidden="true"><use href="#icon-chevron-right"></use></svg></button></div><div id="priorities" class="work-list"></div></section>
      <section class="section"><div class="section-head"><div><p class="eyebrow">РАДАР</p><h2>Скоро важно</h2></div><span id="radar-state" class="count-pill">0</span></div><div id="radar" class="work-list"></div></section>
    </section>
    <section id="view-tasks" class="view" hidden><div class="section-head"><div><p class="eyebrow">TRELLO</p><h2 class="title-with-icon"><span class="title-icon"><svg class="icon" aria-hidden="true"><use href="#icon-list-todo"></use></svg></span>Задачи</h2><p id="tasks-summary" class="section-copy muted"></p></div><button id="open-trello" class="text-button" type="button">Открыть Trello<svg class="icon icon-sm" aria-hidden="true"><use href="#icon-external-link"></use></svg></button></div><div class="task-tools"><label class="task-search-field" for="task-search"><span>Найти задачу</span><span class="input-shell"><svg class="icon" aria-hidden="true"><use href="#icon-search"></use></svg><input id="task-search" type="search" placeholder="Название, P1, Today" autocomplete="off"></span></label><label class="task-list-field" for="task-list-filter"><span>Список</span><select id="task-list-filter" aria-label="Список задач"></select></label></div><div id="task-list" class="work-list"></div></section>
    <section id="view-calendar" class="view" hidden><div class="section-head"><div><p class="eyebrow">7 ДНЕЙ</p><h2 class="title-with-icon"><span class="title-icon"><svg class="icon" aria-hidden="true"><use href="#icon-calendar-days"></use></svg></span>Календарь</h2><p id="calendar-summary" class="section-copy muted"></p></div><button id="open-calendar" class="text-button" type="button">Открыть Calendar<svg class="icon icon-sm" aria-hidden="true"><use href="#icon-external-link"></use></svg></button></div><div id="week-strip" class="week-strip" aria-label="Дни недели"></div><div id="calendar-list" class="cal-timeline"></div></section>
    <section id="view-code" class="view" hidden><div class="section-head"><div><p class="eyebrow eyebrow-warm">CODE DESK</p><h2 class="title-with-icon"><span class="title-icon title-icon-warm"><svg class="icon" aria-hidden="true"><use href="#icon-code-2"></use></svg></span>Работа с кодом</h2><p class="muted section-copy">Дай GitHub-репозиторий для разбора кода или ссылки для проверки гипотезы. Runner вернёт причину, diff и тесты.</p><p class="muted code-guard">Работает в отдельной песочнице: может сделать ветку и commit; push/deploy только после твоего явного подтверждения.</p></div><button id="coding-add" class="primary compact-primary" type="button"><svg class="icon" aria-hidden="true"><use href="#icon-plus"></use></svg>Новая задача</button></div><div id="coding-jobs" class="work-list"></div></section>
    <section id="view-memory" class="view" hidden>
      <section class="section"><div class="section-head"><div><p class="eyebrow">НАПОМИНАНИЯ</p><h2>Ближайшее</h2></div><span id="reminder-count" class="count-pill">0</span></div><div id="reminders" class="work-list"></div></section>
      <section class="section"><div class="section-head"><div><p class="eyebrow">ЗАМЕТКИ</p><h2 class="title-with-icon"><span class="title-icon"><svg class="icon" aria-hidden="true"><use href="#icon-sticky-note"></use></svg></span>Живая память</h2></div></div><label class="search-field" for="note-search"><span>Поиск по заметкам</span><span class="input-shell"><svg class="icon" aria-hidden="true"><use href="#icon-search"></use></svg><input id="note-search" type="search" placeholder="OAuth, Hub_ML, идея..." autocomplete="off"></span></label><div id="notes" class="work-list"></div></section>
      <section class="section"><div class="section-head"><div><p class="eyebrow">ИСТОЧНИКИ</p><h2 class="title-with-icon"><span class="title-icon"><svg class="icon" aria-hidden="true"><use href="#icon-book-open"></use></svg></span>База знаний</h2></div><button id="knowledge-add" class="text-button" type="button"><svg class="icon icon-sm" aria-hidden="true"><use href="#icon-plus"></use></svg>Добавить ссылку</button></div><p class="muted section-copy">Только явно добавленная публичная страница. Сначала preview, потом сохранение.</p><div id="knowledge-sources" class="work-list"></div></section>
      <section class="section status-section"><div class="section-head"><div><p class="eyebrow">СИСТЕМА</p><h2 class="title-with-icon"><span class="title-icon"><svg class="icon" aria-hidden="true"><use href="#icon-radio"></use></svg></span>Статус</h2></div><button id="architecture-open" class="text-button" type="button">Как работает<svg class="icon icon-sm" aria-hidden="true"><use href="#icon-route"></use></svg></button></div><div id="system" class="status-list"></div></section>
    </section>
  </section>
</main>
<button id="quick-add" class="quick-add" type="button" aria-label="Быстро добавить задачу, встречу, напоминание или заметку"><svg class="icon icon-lg" aria-hidden="true"><use href="#icon-plus"></use></svg><span>Добавить</span></button>
<nav id="bottom-nav" class="bottom-nav" aria-label="Навигация"><button class="nav-button is-active" data-view="today" type="button" aria-current="page"><svg class="icon" aria-hidden="true"><use href="#icon-sparkles"></use></svg><span>Сегодня</span></button><button class="nav-button" data-view="tasks" type="button"><svg class="icon" aria-hidden="true"><use href="#icon-list-todo"></use></svg><span>Задачи</span></button><button class="nav-button" data-view="calendar" type="button"><svg class="icon" aria-hidden="true"><use href="#icon-calendar-days"></use></svg><span>План</span></button><button class="nav-button" data-view="code" type="button"><svg class="icon" aria-hidden="true"><use href="#icon-code-2"></use></svg><span>Код</span></button><button class="nav-button" data-view="memory" type="button"><svg class="icon" aria-hidden="true"><use href="#icon-database"></use></svg><span>Память</span></button></nav>
<dialog id="quick-dialog"><form id="quick-form"><p class="eyebrow">ДОБАВИТЬ</p><h2 id="quick-title">Новая задача</h2><div class="quick-types" aria-label="Тип записи"><button class="type-button is-active" data-quick-type="task" type="button"><svg class="icon" aria-hidden="true"><use href="#icon-list-todo"></use></svg>Задача</button><button class="type-button" data-quick-type="event" type="button"><svg class="icon" aria-hidden="true"><use href="#icon-calendar-days"></use></svg>Встреча</button><button class="type-button" data-quick-type="reminder" type="button"><svg class="icon" aria-hidden="true"><use href="#icon-bell"></use></svg>Напомнить</button><button class="type-button" data-quick-type="note" type="button"><svg class="icon" aria-hidden="true"><use href="#icon-sticky-note"></use></svg>Заметка</button></div><label><span id="quick-label">Что сделать</span><span class="voice-field"><textarea id="quick-text" rows="3" maxlength="1000" placeholder="Напиши или надиктуй как есть" required></textarea><button id="voice-toggle" class="voice-button" type="button" aria-label="Надиктовать голосом" hidden><svg class="icon" aria-hidden="true"><use href="#icon-mic"></use></svg></button></span><span id="voice-status" class="voice-status" hidden>Слушаю… говори как есть</span></label><p id="quick-help" class="muted form-help">Задача попадёт в Inbox без приоритета. Это можно изменить позже.</p><label id="quick-project-field" hidden><span>Проект, если нужен</span><input id="quick-project" type="text" placeholder="Hub_ML" maxlength="120"></label><label id="quick-start-field" hidden><span id="quick-start-label">Когда</span><input id="quick-start" type="datetime-local"></label><label id="quick-end-field" hidden><span>До</span><input id="quick-end" type="datetime-local"></label><div class="dialog-actions"><button id="quick-cancel" class="secondary" type="button">Отмена</button><button class="primary" type="submit">Продолжить<svg class="icon" aria-hidden="true"><use href="#icon-chevron-right"></use></svg></button></div></form></dialog>
<dialog id="coding-dialog"><form id="coding-form"><p class="eyebrow">CODE DESK</p><h2>Поставить кодовую задачу</h2><p class="muted form-help">Один preview перед очередью. Runner не получает секреты и не меняет сервер.</p><label><span>Режим</span><select id="coding-mode"><option value="coding">Разобрать GitHub-репозиторий</option><option value="research">Проверить гипотезу</option></select></label><label><span>Что проверить</span><textarea id="coding-prompt" rows="4" maxlength="3000" placeholder="PDF тупит при перелистывании: найди причину и подготовь фикс с тестами" required></textarea></label><label id="coding-repository-field"><span>GitHub-репозиторий</span><input id="coding-repository" type="url" inputmode="url" placeholder="https://github.com/owner/repo" maxlength="500"></label><label id="coding-sources-field" hidden><span>Ссылки для проверки</span><textarea id="coding-sources" rows="3" maxlength="5000" placeholder="По одной HTTPS ссылке в строке"></textarea></label><div class="dialog-actions"><button id="coding-cancel" class="secondary" type="button">Отмена</button><button class="primary" type="submit">К preview</button></div></form></dialog>
<dialog id="task-menu-dialog"><form method="dialog"><p class="eyebrow">ЗАДАЧА</p><h2 id="task-menu-title">Задача</h2><div class="task-menu-actions"><button id="task-menu-move" class="secondary" type="button">Перенести</button><button id="task-menu-priority" class="secondary" type="button">Изменить приоритет</button><button id="task-menu-open" class="secondary" type="button">Открыть в Trello</button></div><div class="dialog-actions"><button id="task-menu-close" class="primary" type="submit">Готово</button></div></form></dialog>
<dialog id="edit-dialog"><form id="edit-form"><p class="eyebrow" id="edit-eyebrow">КОРРЕКТИРОВКА</p><h2 id="edit-title">Изменить</h2><p id="edit-help" class="muted"></p><label id="edit-field-label"><span id="edit-field-name">Значение</span><input id="edit-date" type="datetime-local" hidden><input id="edit-end" type="datetime-local" hidden><select id="edit-choice" hidden></select><textarea id="edit-value" rows="5"></textarea></label><label id="recurrence-field" hidden><span>Повтор</span><select id="edit-recurrence"><option value="keep">Не менять</option><option value="none">Не повторять</option><option value="daily">Каждый день</option><option value="weekly">Каждую неделю</option><option value="monthly">Каждый месяц</option></select></label><div class="dialog-actions"><button id="dialog-cancel" class="secondary" type="button">Отмена</button><button id="dialog-save" class="primary" type="submit">К preview</button></div></form></dialog>
<dialog id="plan-dialog"><form id="plan-form"><p class="eyebrow">ПРОВЕРЬ И ПОДТВЕРДИ</p><h2>План действий</h2><div id="plan-preview" class="preview-list"></div><p class="muted">Изменения применятся один раз после подтверждения.</p><div class="dialog-actions"><button id="plan-cancel" class="secondary" type="button">Отмена</button><button id="plan-execute" class="primary" type="submit">Применить</button></div></form></dialog>
<dialog id="report-dialog"><form method="dialog"><p class="eyebrow">ОТЧЁТ RUNNER</p><h2 id="report-title">Работа</h2><pre id="report-content" class="report-content"></pre><div class="dialog-actions"><button id="report-close" class="primary" type="submit">Закрыть</button></div></form></dialog>
<dialog id="history-dialog"><form method="dialog"><p class="eyebrow">ИСТОРИЯ ЗАМЕТКИ</p><h2 id="history-title">Заметка</h2><div id="history-content" class="preview-list"></div><div class="dialog-actions"><button id="history-close" class="primary" type="submit">Закрыть</button></div></form></dialog>
<dialog id="architecture-dialog"><form method="dialog" class="architecture-sheet"><p class="eyebrow">ЖИВАЯ КАРТА</p><h2>Как запрос проходит через JarHert</h2><p class="muted">Выбери сценарий: маршрут подсветит, куда уходит запрос и где остаётся твоё решение.</p><div class="architecture-scenarios" role="group" aria-label="Сценарий работы"><button class="architecture-scenario" data-architecture-scenario="question" type="button" aria-pressed="false">Вопрос</button><button class="architecture-scenario is-active" data-architecture-scenario="plan" type="button" aria-pressed="true">Задача</button><button class="architecture-scenario" data-architecture-scenario="voice" type="button" aria-pressed="false">Голос</button><button class="architecture-scenario" data-architecture-scenario="research" type="button" aria-pressed="false">Репа</button></div><section id="architecture-flow-path" class="architecture-flow-path" aria-live="polite"><div class="architecture-flow-head"><p id="architecture-flow-eyebrow" class="eyebrow">СЦЕНАРИЙ · ЗАДАЧА</p><h3 id="architecture-flow-title">От задачи до результата</h3><p id="architecture-flow-summary" class="muted"></p></div><div id="architecture-flow-nodes" class="architecture-flow-nodes" aria-label="Маршрут запроса"></div><article class="architecture-detail"><p id="architecture-detail-eyebrow" class="eyebrow">СЕЙЧАС</p><h4 id="architecture-detail-title"></h4><p id="architecture-detail-copy"></p><p id="architecture-detail-guard" class="muted"></p></article></section><div class="dialog-actions"><button class="primary" type="submit">Понятно</button></div></form></dialog>
<dialog id="clip-dialog"><form id="clip-form"><p class="eyebrow">БАЗА ЗНАНИЙ</p><h2>Сохранить ссылку</h2><p class="muted">Страница не скачивается до твоего preview.</p><label><span>Публичный HTTPS URL</span><input id="clip-url" type="url" inputmode="url" placeholder="https://example.com/article" maxlength="2000" required></label><label><span>Проект, если нужен</span><input id="clip-project" type="text" placeholder="Hub_ML" maxlength="120"></label><div id="clip-preview" class="preview-list"></div><div class="dialog-actions"><button id="clip-cancel" class="secondary" type="button">Отмена</button><button id="clip-preview-action" class="secondary" type="submit">К preview</button><button id="clip-execute" class="primary" type="button" hidden>Сохранить</button></div></form></dialog>
<script src="/assets/dashboard.js?v=__ASSET_VERSION__" defer></script></body></html>""".replace("__ASSET_VERSION__", _asset_version())


app = create_app() if os.getenv("JARHERT_DASHBOARD_AUTOSTART") == "1" else None
