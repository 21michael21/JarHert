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
from urllib.parse import parse_qsl, urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

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
        mode = _coding_mode(payload.get("mode"))
        repository_url, source_urls = _coding_context(payload, mode=mode)
        return JSONResponse(
            {
                "request_id": request_id,
                "mode": mode,
                "prompt": prompt,
                "repository_url": repository_url,
                "source_urls": source_urls,
                "preview": _coding_preview(mode=mode, repository_url=repository_url, source_urls=source_urls),
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
        mode = _coding_mode(payload.get("mode"))
        repository_url, source_urls = _coding_context(payload, mode=mode)
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


def _coding_mode(value: Any) -> str:
    mode = str(value or "coding").strip().casefold()
    if mode not in {"coding", "research"}:
        raise HTTPException(status_code=422, detail="unsupported coding mode")
    return mode


def _coding_context(payload: dict[str, Any], *, mode: str) -> tuple[str | None, list[str]]:
    if mode == "coding":
        return _github_repository_url(payload.get("repository_url")), []
    return None, _research_source_urls(payload.get("source_urls"))


def _github_repository_url(value: Any) -> str:
    raw = _required_text(value, label="GitHub репозиторий", limit=500)
    parsed = urlparse(raw)
    parts = [part for part in parsed.path.split("/") if part]
    if (
        parsed.scheme != "https"
        or parsed.hostname != "github.com"
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or len(parts) != 2
    ):
        raise HTTPException(status_code=422, detail="GitHub репозиторий должен быть HTTPS URL вида owner/repo")
    return f"https://github.com/{parts[0]}/{parts[1]}"


def _research_source_urls(value: Any) -> list[str]:
    if not isinstance(value, list) or not value or len(value) > 10:
        raise HTTPException(status_code=422, detail="Добавь от 1 до 10 HTTPS ссылок для проверки")
    urls: list[str] = []
    for item in value:
        try:
            url = validate_archive_url(str(item))
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        if url not in urls:
            urls.append(url)
    if not urls:
        raise HTTPException(status_code=422, detail="Добавь HTTPS ссылку для проверки")
    return urls


def _coding_preview(*, mode: str, repository_url: str | None, source_urls: list[str]) -> list[str]:
    if mode == "research":
        return [
            "Проверить гипотезу по источникам",
            f"Источники: {len(source_urls)}",
            "Runner работает в sandbox; внешние действия только после явного подтверждения.",
        ]
    return [
        "Поставить кодовую задачу в очередь",
        f"Репозиторий: {repository_url}",
        "Runner может подготовить ветку и commit; push/deploy только после отдельного подтверждения.",
    ]


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


def _dashboard_page() -> str:
    return """<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>JarHert</title><link rel="stylesheet" href="/assets/dashboard.css?v=__ASSET_VERSION__"><script src="https://telegram.org/js/telegram-web-app.js?62"></script></head>
<body>
<main class="shell">
  <header class="topbar">
    <div class="brand-lockup"><p class="eyebrow">PERSONAL OS</p><h1>JarHert</h1><p id="last-sync" class="header-status">Соединяюсь с твоим контуром</p></div>
    <div class="top-actions"><span id="mode-chip" class="chip">Быстро</span><button id="refresh" class="icon-button" type="button" aria-label="Обновить данные">Обновить</button></div>
  </header>
  <section id="loading-panel" class="loading-panel" aria-live="polite"><span class="loading-mark" aria-hidden="true"></span><p id="loading-text">Открываю кабинет</p><div class="loading-skeletons" aria-hidden="true"><span></span><span></span><span></span></div></section>
  <section id="cabinet" hidden>
    <div id="notice" class="notice" role="status" aria-live="polite" hidden></div>
    <nav class="view-tabs" aria-label="Разделы">
      <button class="view-tab is-active" data-view="today" type="button" aria-current="page">Сегодня</button><button class="view-tab" data-view="tasks" type="button">Задачи</button><button class="view-tab" data-view="calendar" type="button">Календарь</button><button class="view-tab" data-view="code" type="button">Код</button><button class="view-tab" data-view="memory" type="button">Память</button>
    </nav>
    <section id="view-today" class="view">
      <section class="overview-grid" aria-label="Сводка">
        <button id="overview-tasks" class="overview-tile" data-open-view="tasks" type="button"><span class="overview-label">Задачи</span><strong id="overview-tasks-value">0</strong><span id="overview-tasks-meta" class="overview-meta">на сегодня</span></button>
        <button id="overview-calendar" class="overview-tile" data-open-view="calendar" type="button"><span class="overview-label">Календарь</span><strong id="overview-calendar-value">0</strong><span id="overview-calendar-meta" class="overview-meta">ближайшие 7 дней</span></button>
        <button id="overview-radar" class="overview-tile" data-open-view="memory" type="button"><span class="overview-label">Радар</span><strong id="overview-radar-value">0</strong><span id="overview-radar-meta" class="overview-meta">без новых сигналов</span></button>
      </section>
      <button id="architecture-open-home" class="architecture-teaser" type="button" aria-haspopup="dialog"><span><span class="eyebrow">КАК ЭТО РАБОТАЕТ</span><strong>Проследи путь запроса</strong><small>Выбери сценарий и посмотри живой маршрут до результата.</small></span><span class="architecture-teaser-action">Карта</span></button>
      <article class="focus-card"><div class="focus-topline"><p class="eyebrow">ГЛАВНОЕ СЕЙЧАС</p><span id="focus-state" class="state-dot">Фокус</span></div><h2 id="focus-title">Собираю твой день</h2><p id="focus-meta" class="muted">Задача появится здесь.</p><div class="focus-actions"><button id="focus-done" class="primary" type="button">Отметить готовой</button><button id="focus-move" class="secondary" type="button">Перенести</button></div></article>
      <section class="section"><div class="section-head"><div><p class="eyebrow">ПО РАСПИСАНИЮ</p><h2>Следом</h2></div><button class="text-button" data-open-view="calendar" type="button">Все встречи</button></div><div id="today-calendar" class="timeline"></div></section>
      <section class="section"><div class="section-head"><div><p class="eyebrow">ОЧЕРЕДЬ</p><h2>Три главных</h2></div><button class="text-button" data-open-view="tasks" type="button">Все задачи</button></div><div id="priorities" class="work-list"></div></section>
      <section class="section"><div class="section-head"><div><p class="eyebrow">РАДАР</p><h2>Скоро важно</h2></div><span id="radar-state" class="count-pill">0</span></div><div id="radar" class="work-list"></div></section>
    </section>
    <section id="view-tasks" class="view" hidden><div class="section-head"><div><p class="eyebrow">TRELLO</p><h2>Задачи</h2><p id="tasks-summary" class="section-copy muted"></p></div><button id="open-trello" class="text-button" type="button">Открыть Trello</button></div><div class="task-tools"><label class="task-search-field" for="task-search"><span>Найти задачу</span><input id="task-search" type="search" placeholder="Название, P1, Today" autocomplete="off"></label><label class="task-list-field" for="task-list-filter"><span>Список</span><select id="task-list-filter" aria-label="Список задач"></select></label></div><div id="task-list" class="work-list"></div></section>
    <section id="view-calendar" class="view" hidden><div class="section-head"><div><p class="eyebrow">7 ДНЕЙ</p><h2>Календарь</h2><p id="calendar-summary" class="section-copy muted"></p></div><button id="open-calendar" class="text-button" type="button">Открыть Calendar</button></div><div id="calendar-list" class="timeline"></div></section>
    <section id="view-code" class="view" hidden><div class="section-head"><div><p class="eyebrow">CODE DESK</p><h2>Работа с кодом</h2><p class="muted section-copy">Дай GitHub-репозиторий для разбора кода или ссылки для проверки гипотезы. Runner вернёт причину, diff и тесты.</p><p class="muted code-guard">Работает в отдельной песочнице: может сделать ветку и commit; push/deploy только после твоего явного подтверждения.</p></div><button id="coding-add" class="primary compact-primary" type="button">Новая задача</button></div><div id="coding-jobs" class="work-list"></div></section>
    <section id="view-memory" class="view" hidden>
      <section class="section"><div class="section-head"><div><p class="eyebrow">НАПОМИНАНИЯ</p><h2>Ближайшее</h2></div><span id="reminder-count" class="count-pill">0</span></div><div id="reminders" class="work-list"></div></section>
      <section class="section"><div class="section-head"><div><p class="eyebrow">ЗАМЕТКИ</p><h2>Живая память</h2></div></div><label class="search-field" for="note-search"><span>Поиск по заметкам</span><input id="note-search" type="search" placeholder="OAuth, Hub_ML, идея..." autocomplete="off"></label><div id="notes" class="work-list"></div></section>
      <section class="section"><div class="section-head"><div><p class="eyebrow">ИСТОЧНИКИ</p><h2>База знаний</h2></div><button id="knowledge-add" class="text-button" type="button">Добавить ссылку</button></div><p class="muted section-copy">Только явно добавленная публичная страница. Сначала preview, потом сохранение.</p><div id="knowledge-sources" class="work-list"></div></section>
      <section class="section status-section"><div class="section-head"><div><p class="eyebrow">СИСТЕМА</p><h2>Статус</h2></div><button id="architecture-open" class="text-button" type="button">Как работает</button></div><div id="system" class="status-list"></div></section>
    </section>
  </section>
</main>
<button id="quick-add" class="quick-add" type="button" aria-label="Быстро добавить задачу, встречу, напоминание или заметку"><span aria-hidden="true">+</span><span>Добавить</span></button>
<nav id="bottom-nav" class="bottom-nav" aria-label="Навигация"><button class="nav-button is-active" data-view="today" type="button" aria-current="page">Сегодня</button><button class="nav-button" data-view="tasks" type="button">Задачи</button><button class="nav-button" data-view="calendar" type="button">План</button><button class="nav-button" data-view="code" type="button">Код</button><button class="nav-button" data-view="memory" type="button">Память</button></nav>
<dialog id="quick-dialog"><form id="quick-form"><p class="eyebrow">ДОБАВИТЬ</p><h2 id="quick-title">Новая задача</h2><div class="quick-types" aria-label="Тип записи"><button class="type-button is-active" data-quick-type="task" type="button">Задача</button><button class="type-button" data-quick-type="event" type="button">Встреча</button><button class="type-button" data-quick-type="reminder" type="button">Напомнить</button><button class="type-button" data-quick-type="note" type="button">Заметка</button></div><label><span id="quick-label">Что сделать</span><textarea id="quick-text" rows="3" maxlength="1000" placeholder="Напиши как есть" required></textarea></label><p id="quick-help" class="muted form-help">Задача попадёт в Inbox без приоритета. Это можно изменить позже.</p><label id="quick-project-field" hidden><span>Проект, если нужен</span><input id="quick-project" type="text" placeholder="Hub_ML" maxlength="120"></label><label id="quick-start-field" hidden><span id="quick-start-label">Когда</span><input id="quick-start" type="datetime-local"></label><label id="quick-end-field" hidden><span>До</span><input id="quick-end" type="datetime-local"></label><div class="dialog-actions"><button id="quick-cancel" class="secondary" type="button">Отмена</button><button class="primary" type="submit">Продолжить</button></div></form></dialog>
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
