"""Authenticated Telegram Mini App for the JarHert personal cabinet."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import select
import subprocess
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qsl, urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response

from .knowledge_archive import validate_archive_url
from .mcp_api import NativeToolsAPI
from .dashboard_read_model import build_dashboard_snapshot


ASSET_DIR = Path(__file__).with_name("dashboard_assets")
COOKIE_NAME = "jarhert_dashboard"
SESSION_SECONDS = 12 * 60 * 60
TELEGRAM_AUTH_MAX_AGE_SECONDS = 60 * 60
TELEGRAM_AUTH_FUTURE_SKEW_SECONDS = 5 * 60
CLIP_TOKEN_SECONDS = 15 * 60
PLAN_TOKEN_SECONDS = 15 * 60
SNAPSHOT_CACHE_SECONDS = 60
CODING_TOKEN_SECONDS = 15 * 60
CAUT_CACHE_SECONDS = 5 * 60
CAUT_TIMEOUT_SECONDS = 120  # caut с --provider all гоняет до 16 провайдеров с их собственными таймаутами
CODEX_TIMEOUT_SECONDS = 15
LIMITS_SNAPSHOT_STALE_SECONDS = 15 * 60
LIMITS_INGEST_MAX_BODY = 64 * 1024
LIMITS_INGEST_MAX_ITEMS = 50


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
        if len(session_secret) < 32:
            raise RuntimeError("JARHERT_DASHBOARD_SESSION_SECRET должен содержать минимум 32 символа.")
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

    snapshot_cache: dict[int, tuple[float, dict[str, Any]]] = {}

    def invalidate_snapshot(user_id: int) -> None:
        snapshot_cache.pop(user_id, None)

    @app.get("/", response_class=HTMLResponse)
    async def page() -> HTMLResponse:
        return HTMLResponse(
            _dashboard_page(),
            headers={"Cache-Control": "no-store, max-age=0", "Pragma": "no-cache"},
        )

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
        user_id = require_user(request)
        now = clock()
        cached = snapshot_cache.get(user_id)
        if cached is not None and now - cached[0] < SNAPSHOT_CACHE_SECONDS:
            return JSONResponse(cached[1])
        payload = build_dashboard_snapshot(dashboard_api)
        snapshot_cache[user_id] = (now, payload)
        return JSONResponse(payload)

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

    limits_cache: dict[str, Any] = {"at": 0.0, "payload": None}

    @app.get("/api/limits")
    async def limits(request: Request, refresh: str = "") -> JSONResponse:
        require_user(request)
        now = clock()
        cached = limits_cache["payload"]
        if refresh != "1" and cached is not None and now - limits_cache["at"] < CAUT_CACHE_SECONDS:
            return JSONResponse(cached)
        payload = _collect_limits()
        if payload.get("available"):
            payload["source"] = "live"
        else:
            snapshot = _read_limits_snapshot()
            if snapshot is not None:
                received_at = str(snapshot.get("receivedAt") or "")
                payload = {
                    "available": True,
                    "providers": snapshot.get("providers") or [],
                    "errors": snapshot.get("errors") or [],
                    "generatedAt": snapshot.get("generatedAt"),
                    "receivedAt": received_at,
                    "source": "snapshot",
                    "stale": _limits_snapshot_stale(received_at, now=now),
                }
        limits_cache["at"] = now
        limits_cache["payload"] = payload
        return JSONResponse(payload)

    @app.post("/api/limits/ingest")
    async def limits_ingest(request: Request) -> JSONResponse:
        token = os.getenv("JARHERT_LIMITS_INGEST_TOKEN", "").strip()
        if not token:
            raise HTTPException(status_code=404, detail="limits ingest is disabled")
        scheme, _, presented = request.headers.get("authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not presented or not hmac.compare_digest(presented, token):
            raise HTTPException(status_code=401, detail="invalid ingest token")
        body = await request.body()
        if len(body) > LIMITS_INGEST_MAX_BODY:
            raise HTTPException(status_code=422, detail="payload too large")
        try:
            raw = json.loads(body)
        except ValueError:
            raise HTTPException(status_code=422, detail="invalid JSON") from None
        snapshot = _validate_limits_snapshot(raw)
        received_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        snapshot["receivedAt"] = received_at
        try:
            _write_limits_snapshot(snapshot)
        except OSError as error:
            raise HTTPException(status_code=503, detail="snapshot write failed") from error
        return JSONResponse({"ok": True, "receivedAt": received_at})

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
        invalidate_snapshot(user_id)
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

    @app.post("/api/notes")
    async def create_note(request: Request) -> JSONResponse:
        user_id = require_user(request)
        invalidate_snapshot(user_id)
        payload = await _request_payload(request)
        subject = _required_text(payload.get("subject"), label="Тема заметки", limit=200)
        content = _required_text(payload.get("content"), label="Текст заметки", limit=4000)
        project = _optional_text(payload.get("project"), limit=120)
        return JSONResponse(
            _call_write(
                lambda: dashboard_api.memory_block_upsert(
                    block_type="note",
                    subject=subject,
                    content=content,
                    project=project,
                )
            )
        )

    @app.post("/api/reminders")
    async def create_reminder(request: Request) -> JSONResponse:
        user_id = require_user(request)
        invalidate_snapshot(user_id)
        payload = await _request_payload(request)
        request_id = _request_id(payload.get("request_id"))
        text = _required_text(payload.get("text"), label="Текст напоминания", limit=500)
        remind_at = _required_text(payload.get("remind_at"), label="Время напоминания", limit=80)
        recurrence = str(payload.get("recurrence") or "none")
        if recurrence not in {"none", "daily", "weekly", "monthly"}:
            raise HTTPException(status_code=422, detail="unknown recurrence")
        return JSONResponse(
            _call_write(
                lambda: dashboard_api.reminder_create(
                    text=text,
                    remind_at=remind_at,
                    recurrence=recurrence,
                    idempotency_key=f"dashboard:reminder:{user_id}:{request_id}",
                )
            )
        )

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

    @app.get("/api/expenses")
    async def expenses(request: Request, limit: int = 30) -> JSONResponse:
        require_user(request)
        bounded_limit = max(1, min(int(limit), 100))
        return JSONResponse(_call_read(lambda: dashboard_api.expense_list(limit=bounded_limit)))

    @app.get("/api/expenses/monthly")
    async def expenses_monthly(request: Request, month: str | None = None) -> JSONResponse:
        require_user(request)
        clean_month = str(month or "").strip() or None
        if clean_month is not None and not re.fullmatch(r"\d{4}-\d{2}", clean_month):
            raise HTTPException(status_code=422, detail="month must be YYYY-MM")
        return JSONResponse(_call_read(lambda: dashboard_api.expense_monthly(month=clean_month)))

    @app.get("/api/commitments")
    async def commitments(request: Request) -> JSONResponse:
        require_user(request)
        return JSONResponse(_call_read(lambda: dashboard_api.commitment_list(status="open", limit=50)))

    @app.post("/api/commitments/{commitment_id}/complete")
    async def complete_commitment(commitment_id: int, request: Request) -> JSONResponse:
        user_id = require_user(request)
        invalidate_snapshot(user_id)
        return JSONResponse(_call_write(lambda: dashboard_api.commitment_complete(commitment_id=_positive_id(commitment_id))))

    @app.get("/api/trips")
    async def trips(request: Request) -> JSONResponse:
        require_user(request)
        def collect() -> dict[str, Any]:
            result = dashboard_api.trip_list(status="active", limit=10)
            for trip in result.get("items", []):
                details = dashboard_api.trip_details(trip_id=int(trip["id"]))
                items = details.get("items", [])
                open_items = [entry for entry in items if entry.get("status") == "open"]
                trip["open_items"] = len(open_items)
                trip["total_items"] = len(items)
            return result
        return JSONResponse(_call_read(collect))

    @app.get("/api/projects/status")
    async def project_status(request: Request, project: str = "") -> JSONResponse:
        require_user(request)
        clean_project = " ".join(str(project or "").split())
        if not clean_project:
            raise HTTPException(status_code=422, detail="project is required")
        return JSONResponse(_call_read(lambda: dashboard_api.project_status_report(project=clean_project)))

    @app.get("/api/projects")
    async def projects(request: Request) -> JSONResponse:
        require_user(request)
        return JSONResponse(_call_read(dashboard_api.project_context_list))

    @app.get("/api/search")
    async def global_search(request: Request, query: str = "") -> JSONResponse:
        require_user(request)
        clean_query = " ".join(str(query or "").split())
        if not clean_query:
            return JSONResponse({"notes": [], "knowledge": []})
        def collect() -> dict[str, Any]:
            notes = dashboard_api.note_search(query=clean_query, limit=8).get("items", [])
            knowledge = dashboard_api.knowledge_search(query=clean_query, limit=8).get("items", [])
            return {"notes": notes, "knowledge": knowledge}
        return JSONResponse(_call_read(collect))

    @app.get("/api/export")
    async def export_data(request: Request) -> Response:
        require_user(request)
        def collect() -> dict[str, Any]:
            notes = dashboard_api.memory_block_list(block_type="note", limit=500).get("items", [])
            expenses = dashboard_api.expense_list(limit=500).get("items", [])
            return {
                "exported_at": datetime.now(timezone.utc).isoformat(),
                "notes": notes,
                "expenses": expenses,
            }
        return JSONResponse(
            _call_read(collect),
            headers={"Content-Disposition": 'attachment; filename="jarhert-export.json"'},
        )

    @app.post("/api/expenses")
    async def add_expense(request: Request) -> JSONResponse:
        user_id = require_user(request)
        invalidate_snapshot(user_id)
        payload = await _request_payload(request)
        request_id = _request_id(payload.get("request_id"))
        text = _required_text(payload.get("text"), label="Трата", limit=200)
        try:
            amount = float(payload.get("amount"))
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail="amount must be a number")
        currency = str(payload.get("currency") or "RUB").strip().upper()[:10] or "RUB"
        category = _optional_text(payload.get("category"), limit=60)
        project = _optional_text(payload.get("project"), limit=120)
        return JSONResponse(
            _call_write(
                lambda: dashboard_api.expense_add(
                    text=text,
                    amount=amount,
                    currency=currency,
                    category=category,
                    project=project,
                    idempotency_key=f"dashboard:expense:{user_id}:{request_id}",
                )
            )
        )

    @app.get("/api/monitors/digest")
    async def monitor_digest(request: Request) -> JSONResponse:
        require_user(request)
        return JSONResponse(_call_read(dashboard_api.monitor_digest))

    @app.put("/api/monitors/{monitor_id}/schedule")
    async def update_monitor_schedule(monitor_id: int, request: Request) -> JSONResponse:
        invalidate_snapshot(require_user(request))
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
        invalidate_snapshot(user_id)
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
        invalidate_snapshot(user_id)
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
                "plan_token": _new_plan_token(user_id, plan_id, config.session_secret, clock=clock),
                "preview": _plan_preview(plan),
            }
        )

    @app.post("/api/plans/{plan_id}/execute")
    async def execute_plan(plan_id: int, request: Request) -> JSONResponse:
        user_id = require_user(request)
        invalidate_snapshot(user_id)
        payload = await _request_payload(request)
        safe_plan_id = _positive_id(plan_id)
        _require_plan_token(payload.get("plan_token"), user_id, safe_plan_id, config.session_secret, clock=clock)
        return JSONResponse(_call_write(lambda: dashboard_api.action_plan_execute(plan_id=safe_plan_id, confirmed=True)))

    @app.post("/api/plans/{plan_id}/cancel")
    async def cancel_plan(plan_id: int, request: Request) -> JSONResponse:
        user_id = require_user(request)
        invalidate_snapshot(user_id)
        payload = await _request_payload(request)
        safe_plan_id = _positive_id(plan_id)
        _require_plan_token(payload.get("plan_token"), user_id, safe_plan_id, config.session_secret, clock=clock)
        return JSONResponse(_call_write(lambda: dashboard_api.action_plan_cancel(plan_id=safe_plan_id)))

    @app.post("/api/reminders/{reminder_id}/reschedule")
    async def reschedule_reminder(reminder_id: int, request: Request) -> JSONResponse:
        invalidate_snapshot(require_user(request))
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
        invalidate_snapshot(require_user(request))
        return JSONResponse(_call_write(lambda: dashboard_api.reminder_cancel(reminder_id=_positive_id(reminder_id))))

    @app.put("/api/notes/{note_id}")
    async def edit_note(note_id: int, request: Request) -> JSONResponse:
        invalidate_snapshot(require_user(request))
        payload = await _request_payload(request)
        content = _required_text(payload.get("content"), label="Текст заметки", limit=4000)
        return JSONResponse(_call_write(lambda: dashboard_api.note_edit(note_id=_positive_id(note_id), content=content)))

    @app.delete("/api/notes/{note_id}")
    async def delete_note(note_id: int, request: Request) -> JSONResponse:
        invalidate_snapshot(require_user(request))
        return JSONResponse(_call_write(lambda: dashboard_api.note_delete(note_id=_positive_id(note_id))))

    @app.get("/assets/{asset_name}")
    async def asset(asset_name: str) -> FileResponse:
        allowed = {"dashboard.css", "dashboard.js"}
        if asset_name not in allowed:
            raise HTTPException(status_code=404, detail="asset not found")
        return FileResponse(ASSET_DIR / asset_name, headers={"Cache-Control": "public, max-age=31536000, immutable"})

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


def _new_plan_token(user_id: int, plan_id: int, secret: str, *, clock: Callable[[], float]) -> str:
    expires_at = int(clock()) + PLAN_TOKEN_SECONDS
    payload = f"dashboard-plan.{int(user_id)}.{int(plan_id)}.{expires_at}"
    signature = hmac.new(secret.encode("utf-8"), payload.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}"


def _require_plan_token(
    token: Any, user_id: int, plan_id: int, secret: str, *, clock: Callable[[], float]
) -> None:
    parts = (token or "").split(".") if isinstance(token, str) else []
    detail = "plan confirmation is not valid for this session"
    if len(parts) != 5 or parts[0] != "dashboard-plan":
        raise HTTPException(status_code=403, detail=detail)
    _, token_user, token_plan, expires_at, signature = parts
    if not expires_at.isdigit() or int(expires_at) < int(clock()):
        raise HTTPException(status_code=403, detail="plan confirmation expired")
    payload = ".".join(parts[:4])
    expected = hmac.new(secret.encode("utf-8"), payload.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected) or token_user != str(int(user_id)) or token_plan != str(int(plan_id)):
        raise HTTPException(status_code=403, detail=detail)


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


def _limits_snapshot_path() -> Path:
    home = os.getenv("HERMES_HOME", "").strip()
    base = Path(home) if home else Path(tempfile.gettempdir())
    return base / "limits_snapshot.json"


def _validate_limits_snapshot(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="payload must be a JSON object")
    providers = payload.get("providers")
    if (
        not isinstance(providers, list)
        or len(providers) > LIMITS_INGEST_MAX_ITEMS
        or not all(isinstance(item, dict) for item in providers)
    ):
        raise HTTPException(status_code=422, detail="providers must be a list of up to 50 objects")
    errors = payload.get("errors", [])
    if (
        not isinstance(errors, list)
        or len(errors) > LIMITS_INGEST_MAX_ITEMS
        or not all(isinstance(item, (str, dict)) for item in errors)
    ):
        raise HTTPException(status_code=422, detail="errors must be a list of up to 50 strings or objects")
    generated_at = payload.get("generatedAt")
    if generated_at is not None and (not isinstance(generated_at, str) or len(generated_at) > 64):
        raise HTTPException(status_code=422, detail="generatedAt must be a string up to 64 chars")
    return {"providers": providers, "errors": errors, "generatedAt": generated_at}


def _write_limits_snapshot(snapshot: dict[str, Any]) -> None:
    path = _limits_snapshot_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".limits_snapshot.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(snapshot, handle, ensure_ascii=False)
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _read_limits_snapshot() -> dict[str, Any] | None:
    try:
        payload = json.loads(_limits_snapshot_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("providers"), list):
        return None
    return payload


def _limits_snapshot_stale(received_at: str, *, now: float) -> bool:
    if not received_at:
        return True
    try:
        parsed = datetime.fromisoformat(received_at.replace("Z", "+00:00"))
    except ValueError:
        return True
    return now - parsed.timestamp() > LIMITS_SNAPSHOT_STALE_SECONDS


def _collect_limits() -> dict[str, Any]:
    """Merge Codex app-server rate limits with caut output. Never raises."""
    failures: list[str] = []
    codex_cards: list[dict[str, Any]] = []
    codex = _collect_codex_rate_limits()
    if codex["available"]:
        codex_cards = codex["providers"]
    else:
        failures.append(_source_failure("codex app-server", codex))
    caut = _collect_caut_usage()
    caut_providers: list[dict[str, Any]] = []
    caut_errors: list[dict[str, Any]] = []
    generated_at: str | None = None
    if caut["available"]:
        generated_at = caut.get("generatedAt")
        for entry in caut.get("errors") or []:
            if codex_cards and str(entry.get("provider") or "").lower() == "codex":
                continue  # ошибка caut по codex неактуальна — данные уже дал app-server
            caut_errors.append(entry)
        for provider in caut.get("providers") or []:
            name = str(provider.get("provider") or "").lower()
            if name == "codex":
                if codex_cards:
                    continue  # замещено живыми данными app-server
            elif not _caut_provider_has_data(provider):
                continue
            caut_providers.append(provider)
    else:
        failures.append(_source_failure("caut", caut))
    if not codex["available"] and not caut["available"]:
        return {"available": False, "reason": "no_sources", "detail": "; ".join(failures)[:500]}
    return {
        "available": True,
        "providers": codex_cards + caut_providers,
        "errors": caut_errors,
        "generatedAt": generated_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


def _source_failure(name: str, result: dict[str, Any]) -> str:
    reason = str(result.get("reason") or "error")
    detail = str(result.get("detail") or "").strip()
    return f"{name}: {reason} ({detail[:200]})" if detail else f"{name}: {reason}"


def _caut_provider_has_data(provider: dict[str, Any]) -> bool:
    usage = provider.get("usage")
    if isinstance(usage, dict):
        for key in ("primary", "secondary", "tertiary"):
            if isinstance(usage.get(key), dict) and usage[key]:
                return True
        if usage.get("identity"):
            return True
    return bool(provider.get("credits") or provider.get("account"))


def _collect_codex_rate_limits() -> dict[str, Any]:
    """Query the Codex app-server over stdio JSON-RPC. Never raises."""
    binary = os.getenv("JARHERT_CODEX_BIN", "").strip() or "codex"
    try:
        process = subprocess.Popen(
            [binary, "app-server"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except FileNotFoundError:
        return {"available": False, "reason": "codex_not_installed"}
    except OSError as error:
        return {"available": False, "reason": "error", "detail": str(error)[:500]}
    try:
        result = _codex_rate_limits_rpc(process)
    except TimeoutError:
        return {"available": False, "reason": "timeout"}
    except (OSError, ValueError) as error:
        return {"available": False, "reason": "error", "detail": str(error)[:500]}
    finally:
        _stop_process(process)
    cards = _codex_provider_cards(result)
    if not cards:
        return {"available": False, "reason": "error", "detail": "codex app-server вернул пустые rate limits"}
    return {"available": True, "providers": cards}


def _codex_rate_limits_rpc(process: subprocess.Popen[Any]) -> dict[str, Any]:
    if process.stdin is None or process.stdout is None:
        raise ValueError("codex app-server stdio недоступен")
    messages = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"clientInfo": {"name": "jarhert-cabinet", "version": "1.0"}}},
        {"jsonrpc": "2.0", "method": "initialized", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "account/rateLimits/read", "params": {}},
    ]
    for message in messages:
        process.stdin.write(json.dumps(message) + "\n")
    process.stdin.flush()
    # stdin НЕ закрываем: app-server завершается по EOF и не успеет ответить.
    # Процесс гасится через _stop_process в finally после получения ответа.
    deadline = time.monotonic() + CODEX_TIMEOUT_SECONDS
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"codex app-server не ответил за {CODEX_TIMEOUT_SECONDS} секунд")
        ready, _, _ = select.select([process.stdout], [], [], remaining)
        if not ready:
            raise TimeoutError(f"codex app-server не ответил за {CODEX_TIMEOUT_SECONDS} секунд")
        line = process.stdout.readline()
        if not line:
            raise ValueError("codex app-server закрыл соединение без ответа")
        try:
            message = json.loads(line)
        except ValueError:
            continue  # пропускаем мусорные строки в stdout
        if not isinstance(message, dict) or message.get("id") != 2:
            continue  # notifications и ответ на initialize
        if message.get("error"):
            raise ValueError(str(message["error"])[:500])
        result = message.get("result")
        if not isinstance(result, dict):
            raise ValueError("codex app-server вернул пустой result")
        return result


def _stop_process(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()


def _codex_provider_cards(result: dict[str, Any]) -> list[dict[str, Any]]:
    entries: list[Any] = []
    by_limit = result.get("rateLimitsByLimitId")
    if isinstance(by_limit, dict) and by_limit:
        entries = list(by_limit.values())
    elif isinstance(result.get("rateLimits"), dict):
        entries = [result["rateLimits"]]
    cards = [card for card in (_codex_provider_card(entry) for entry in entries) if card is not None]
    reset_credits = result.get("rateLimitResetCredits")
    if isinstance(reset_credits, dict):
        count = reset_credits.get("availableCount")
        if isinstance(count, (int, float)) and not isinstance(count, bool) and count > 0:
            for card in cards:
                if card["provider"] == "codex":
                    card["resetCredits"] = int(count)
                    break
    return cards


def _codex_provider_card(entry: Any) -> dict[str, Any] | None:
    if not isinstance(entry, dict):
        return None
    limit_id = str(entry.get("limitId") or "codex")
    limit_name = str(entry.get("limitName") or "").strip()
    card: dict[str, Any] = {
        "provider": "codex" if limit_id == "codex" else f"codex · {limit_name or limit_id}",
        "source": "app-server",
    }
    plan = str(entry.get("planType") or "").strip()
    if plan:
        card["plan"] = plan
    usage: dict[str, Any] = {}
    for key in ("primary", "secondary"):
        window = entry.get(key)
        if not isinstance(window, dict):
            continue
        mapped: dict[str, Any] = {}
        used = window.get("usedPercent")
        if isinstance(used, (int, float)) and not isinstance(used, bool):
            mapped["usedPercent"] = used
            mapped["remainingPercent"] = max(0, 100 - used)
        duration = window.get("windowDurationMins")
        if isinstance(duration, (int, float)) and not isinstance(duration, bool):
            mapped["windowMinutes"] = int(duration)
        resets_at = window.get("resetsAt")
        if isinstance(resets_at, (int, float)) and not isinstance(resets_at, bool):
            mapped["resetsAt"] = datetime.fromtimestamp(resets_at, tz=timezone.utc).isoformat().replace("+00:00", "Z")
        if mapped:
            usage[key] = mapped
    if usage:
        card["usage"] = usage
    credits = entry.get("credits")
    if isinstance(credits, dict) and credits.get("hasCredits"):
        try:
            card["credits"] = {"remaining": float(credits.get("balance") or 0)}
        except (TypeError, ValueError):
            pass
    return card


def _collect_caut_usage() -> dict[str, Any]:
    """Run `caut usage --provider all --json` and shape the result for the cabinet.

    Never raises: every failure mode degrades to {"available": False, ...}.
    """
    binary = os.getenv("JARHERT_CAUT_BIN", "").strip() or "caut"
    try:
        result = subprocess.run(
            [binary, "usage", "--provider", "all", "--json"],
            capture_output=True,
            text=True,
            timeout=CAUT_TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError:
        return {"available": False, "reason": "caut_not_installed"}
    except subprocess.TimeoutExpired:
        return {"available": False, "reason": "timeout"}
    except OSError as error:
        return {"available": False, "reason": "error", "detail": str(error)[:500]}
    if result.returncode != 0:
        detail = (result.stderr or "").strip()[:500] or f"caut завершился с кодом {result.returncode}"
        return {"available": False, "reason": "error", "detail": detail}
    try:
        report = json.loads(result.stdout)
    except ValueError:
        return {"available": False, "reason": "error", "detail": "caut вернул невалидный JSON"}
    if not isinstance(report, dict):
        return {"available": False, "reason": "error", "detail": "caut вернул невалидный JSON"}
    schema = report.get("schemaVersion")
    if schema is not None and schema != "caut.v1":
        return {"available": False, "reason": "error", "detail": f"неподдерживаемая версия схемы caut: {schema}"}
    providers = [item for item in (report.get("data") or []) if isinstance(item, dict)]
    errors = [item for item in (report.get("errors") or []) if isinstance(item, dict)]
    return {
        "available": True,
        "providers": providers,
        "errors": errors,
        "generatedAt": report.get("generatedAt"),
    }


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
  <symbol id="icon-wallet-cards" viewBox="0 0 24 24"><rect width="18" height="18" x="3" y="3" rx="2"/><path d="M3 9h18M7 15h.01M11 15h2"/></symbol>
  <symbol id="icon-gauge" viewBox="0 0 24 24"><path d="m12 14 4-4M3.3 19a10 10 0 1 1 17.4 0"/><path d="M6.7 17h10.6"/></symbol>
  <symbol id="icon-layout-grid" viewBox="0 0 24 24"><rect width="7" height="7" x="3" y="3" rx="1"/><rect width="7" height="7" x="14" y="3" rx="1"/><rect width="7" height="7" x="3" y="14" rx="1"/><rect width="7" height="7" x="14" y="14" rx="1"/></symbol>
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
</svg>
<main class="shell">
  <header class="topbar">
    <div class="brand-lockup"><p class="eyebrow">PERSONAL OS</p><h1>JarHert</h1><p id="last-sync" class="header-status">Соединяюсь с твоим контуром</p></div>
    <div class="top-actions"><span id="mode-chip" class="chip" hidden><svg class="icon icon-warm" aria-hidden="true"><use href="#icon-sparkles"></use></svg><span id="mode-chip-label">Быстро</span></span><button id="refresh" class="icon-button" type="button" aria-label="Обновить данные"><svg class="icon" aria-hidden="true"><use href="#icon-refresh-cw"></use></svg></button></div>
  </header>
  <section id="loading-panel" class="loading-panel" aria-live="polite"><span class="loading-mark" aria-hidden="true"></span><p id="loading-text">Открываю кабинет</p><div class="loading-skeletons" aria-hidden="true"><span></span><span></span><span></span></div></section>
  <section id="cabinet" hidden>
    <div id="notice" class="notice" role="status" aria-live="polite" hidden></div>
    <nav class="view-tabs" aria-label="Разделы">
      <button class="view-tab is-active" data-view="today" type="button" aria-current="page"><svg class="icon icon-sm" aria-hidden="true"><use href="#icon-sparkles"></use></svg>Сегодня</button><button class="view-tab" data-view="tasks" type="button"><svg class="icon icon-sm" aria-hidden="true"><use href="#icon-list-todo"></use></svg>Задачи</button><button class="view-tab" data-view="calendar" type="button"><svg class="icon icon-sm" aria-hidden="true"><use href="#icon-calendar-days"></use></svg>Календарь</button><button class="view-tab" data-view="money" type="button"><svg class="icon icon-sm" aria-hidden="true"><use href="#icon-wallet-cards"></use></svg>Деньги</button><button class="view-tab" data-view="code" type="button"><svg class="icon icon-sm" aria-hidden="true"><use href="#icon-code-2"></use></svg>Код</button><button class="view-tab" data-view="memory" type="button"><svg class="icon icon-sm" aria-hidden="true"><use href="#icon-database"></use></svg>Память</button><button class="view-tab" data-view="limits" type="button"><svg class="icon icon-sm" aria-hidden="true"><use href="#icon-gauge"></use></svg>Лимиты</button>
    </nav>
    <section id="view-today" class="view">
      <section class="overview-grid" aria-label="Сводка">
        <button id="overview-tasks" class="overview-tile" data-open-view="tasks" type="button"><span class="overview-heading"><svg class="icon" aria-hidden="true"><use href="#icon-list-todo"></use></svg><span class="overview-label">Задачи</span></span><strong id="overview-tasks-value">0</strong><span id="overview-tasks-meta" class="overview-meta">на сегодня</span></button>
        <button id="overview-calendar" class="overview-tile" data-open-view="calendar" type="button"><span class="overview-heading"><svg class="icon" aria-hidden="true"><use href="#icon-calendar-days"></use></svg><span class="overview-label">Календарь</span></span><strong id="overview-calendar-value">0</strong><span id="overview-calendar-meta" class="overview-meta">ближайшие 7 дней</span></button>
        <button id="overview-radar" class="overview-tile" data-open-view="memory" type="button"><span class="overview-heading"><svg class="icon" aria-hidden="true"><use href="#icon-radar"></use></svg><span class="overview-label">Радар</span></span><strong id="overview-radar-value">0</strong><span id="overview-radar-meta" class="overview-meta">без новых сигналов</span></button>
      </section>
      <button id="architecture-open-home" class="architecture-teaser" type="button" aria-haspopup="dialog"><span class="architecture-teaser-icon"><svg class="icon icon-lg" aria-hidden="true"><use href="#icon-route"></use></svg></span><span><span class="eyebrow">КАК ЭТО РАБОТАЕТ</span><strong>Проследи путь запроса</strong><small>Выбери сценарий и посмотри живой маршрут до результата.</small></span><span class="architecture-teaser-action">Карта<svg class="icon icon-sm" aria-hidden="true"><use href="#icon-chevron-right"></use></svg></span></button>
      <article class="focus-card"><div class="focus-topline"><p class="eyebrow">ГЛАВНОЕ СЕЙЧАС</p><span id="focus-state" class="state-dot">Фокус</span></div><h2 id="focus-title">Собираю твой день</h2><p id="focus-meta" class="muted">Задача появится здесь.</p><div class="focus-actions"><button id="focus-done" class="primary" type="button"><svg class="icon" aria-hidden="true"><use href="#icon-check"></use></svg>Отметить готовой</button><button id="focus-move" class="secondary" type="button"><svg class="icon" aria-hidden="true"><use href="#icon-clock"></use></svg>Перенести</button></div></article>
      <section class="section"><div class="section-head"><div><p class="eyebrow">ПО РАСПИСАНИЮ</p><h2>Следом</h2></div><button class="text-button" data-open-view="calendar" type="button">Все встречи<svg class="icon icon-sm" aria-hidden="true"><use href="#icon-chevron-right"></use></svg></button></div><div id="today-calendar" class="timeline"></div></section>
      <section class="section"><div class="section-head"><div><p class="eyebrow">ОЧЕРЕДЬ</p><h2>Три главных</h2></div><button class="text-button" data-open-view="tasks" type="button">Все задачи<svg class="icon icon-sm" aria-hidden="true"><use href="#icon-chevron-right"></use></svg></button></div><div id="priorities" class="work-list"></div></section>
      <section class="section"><div class="section-head"><div><h2>Скоро важно</h2></div><span id="radar-state" class="count-pill">0</span></div><div id="radar" class="work-list"></div></section>
    </section>
    <section id="view-tasks" class="view" hidden><div class="section-head"><div><p class="eyebrow">TRELLO</p><h2 class="title-with-icon"><span class="title-icon"><svg class="icon" aria-hidden="true"><use href="#icon-list-todo"></use></svg></span>Задачи</h2><p id="tasks-summary" class="section-copy muted"></p></div><button id="open-trello" class="text-button" type="button">Открыть Trello<svg class="icon icon-sm" aria-hidden="true"><use href="#icon-external-link"></use></svg></button></div><div class="task-tools"><label class="task-search-field" for="task-search"><span>Найти задачу</span><span class="input-shell"><svg class="icon" aria-hidden="true"><use href="#icon-search"></use></svg><input id="task-search" type="search" placeholder="Название, P1, Today" autocomplete="off"></span></label><label class="task-list-field" for="task-list-filter"><span>Список</span><select id="task-list-filter" aria-label="Список задач"></select></label></div><div id="task-list" class="work-list"></div></section>
    <section id="view-calendar" class="view" hidden><div class="section-head"><div><p class="eyebrow">7 ДНЕЙ</p><h2 class="title-with-icon"><span class="title-icon"><svg class="icon" aria-hidden="true"><use href="#icon-calendar-days"></use></svg></span>Календарь</h2><p id="calendar-summary" class="section-copy muted"></p></div><button id="open-calendar" class="text-button" type="button">Открыть Calendar<svg class="icon icon-sm" aria-hidden="true"><use href="#icon-external-link"></use></svg></button></div><div id="calendar-list" class="timeline"></div></section>
    <section id="view-code" class="view" hidden><div class="section-head"><div><p class="eyebrow eyebrow-warm">CODE DESK</p><h2 class="title-with-icon"><span class="title-icon title-icon-warm"><svg class="icon" aria-hidden="true"><use href="#icon-code-2"></use></svg></span>Работа с кодом</h2><p class="muted section-copy">Дай GitHub-репозиторий для разбора кода или ссылки для проверки гипотезы. Runner вернёт причину, diff и тесты.</p><p class="muted code-guard">Работает в отдельной песочнице: может сделать ветку и commit; push/deploy только после твоего явного подтверждения.</p></div><button id="coding-add" class="primary compact-primary" type="button"><svg class="icon" aria-hidden="true"><use href="#icon-plus"></use></svg>Новая задача</button></div><p id="runner-status" class="runner-status" data-tone="muted">Проверяю раннер…</p><div id="coding-jobs" class="work-list"></div></section>
    <section id="view-money" class="view" hidden>
      <div class="section-head"><div><p class="eyebrow">ДЕНЬГИ</p><h2 class="title-with-icon"><span class="title-icon title-icon-good"><svg class="icon" aria-hidden="true"><use href="#icon-wallet-cards"></use></svg></span>Финансы месяца</h2><p id="money-summary" class="section-copy muted"></p></div><button id="expense-add" class="text-button" type="button"><svg class="icon icon-sm" aria-hidden="true"><use href="#icon-plus"></use></svg>Добавить трату</button></div>
      <section id="money-bars-section" class="section"><div class="section-head"><div><h2>По категориям</h2></div></div><div id="money-bars" class="money-bars"></div></section>
      <section id="money-week-section" class="section"><div class="section-head"><div><h2>Траты по дням</h2></div><span id="week-total" class="count-pill"></span></div><div id="money-week" class="money-week"></div></section>
      <section id="subscriptions-section" class="section"><div class="section-head"><div><h2>Регулярные списания</h2></div><span id="subscriptions-total" class="count-pill"></span></div><div id="subscriptions" class="work-list"></div></section>
      <section id="expenses-section" class="section"><div class="section-head"><div><h2>Последние записи</h2></div><span id="expense-count" class="count-pill">0</span></div><div id="expenses" class="work-list"></div></section>
    </section>
    <section id="view-memory" class="view" hidden>
      <section class="section"><div class="section-head"><div><p class="eyebrow">ПОИСК</p><h2 class="title-with-icon"><span class="title-icon"><svg class="icon" aria-hidden="true"><use href="#icon-search"></use></svg></span>Найти всё</h2></div></div><label class="search-field" for="global-search"><span>Заметки, знания, задачи</span><span class="input-shell"><svg class="icon" aria-hidden="true"><use href="#icon-search"></use></svg><input id="global-search" type="search" placeholder="Одна строка — вся память" autocomplete="off"></span></label><div id="search-results" class="work-list"></div></section>
      <details class="section" open><summary class="section-head"><div><h2 class="summary-title"><svg class="icon" aria-hidden="true"><use href="#icon-sticky-note"></use></svg>Живая память</h2></div><span class="summary-side"></span></summary><div class="section-actions"><button id="note-add" class="text-button" type="button"><svg class="icon icon-sm" aria-hidden="true"><use href="#icon-plus"></use></svg>Новая заметка</button></div><label class="search-field" for="note-search"><span>Поиск по заметкам</span><span class="input-shell"><svg class="icon" aria-hidden="true"><use href="#icon-search"></use></svg><input id="note-search" type="search" placeholder="OAuth, Hub_ML, идея..." autocomplete="off"></span></label><div id="notes" class="work-list"></div></details>
      <details class="section" open><summary class="section-head"><div><h2 class="summary-title"><svg class="icon" aria-hidden="true"><use href="#icon-bell"></use></svg>Напоминания</h2></div><span class="summary-side"><span id="reminder-count" class="count-pill">0</span></span></summary><div class="section-actions"><button id="reminder-add" class="text-button" type="button"><svg class="icon icon-sm" aria-hidden="true"><use href="#icon-plus"></use></svg>Новое напоминание</button></div><div id="reminders" class="work-list"></div></details>
      <details class="section"><summary class="section-head"><div><h2>Держу слово</h2></div><span class="summary-side"><span id="commitment-count" class="count-pill">0</span></span></summary><div id="commitments" class="work-list"></div></details>
      <details class="section"><summary class="section-head"><div><h2 class="summary-title"><svg class="icon" aria-hidden="true"><use href="#icon-book-open"></use></svg>База знаний</h2></div><span class="summary-side"></span></summary><div class="section-actions"><button id="knowledge-add" class="text-button" type="button"><svg class="icon icon-sm" aria-hidden="true"><use href="#icon-plus"></use></svg>Добавить ссылку</button></div><p class="muted section-copy">Только явно добавленная публичная страница. Сначала preview, потом сохранение.</p><div id="knowledge-sources" class="work-list"></div></details>
      <details class="section"><summary class="section-head"><div><h2>Собранность</h2></div><span class="summary-side"></span></summary><div id="trips" class="work-list"></div></details>
      <details class="section"><summary class="section-head"><div><h2>Статус для команды</h2></div><span class="summary-side"></span></summary><div class="section-actions"><button id="project-copy" class="text-button" type="button" disabled>Копировать</button></div><label class="search-field" for="project-select"><span>Проект</span><select id="project-select"></select></label><div id="project-status" class="work-list"></div></details>
      <details class="section status-section"><summary class="section-head"><div><h2 class="summary-title"><svg class="icon" aria-hidden="true"><use href="#icon-radio"></use></svg>Система</h2></div><span class="summary-side"></span></summary><div class="section-actions"><a id="data-export" class="text-button" href="/api/export" download><svg class="icon icon-sm" aria-hidden="true"><use href="#icon-file-text"></use></svg>Экспорт</a><button id="architecture-open" class="text-button" type="button">Как работает<svg class="icon icon-sm" aria-hidden="true"><use href="#icon-route"></use></svg></button></div><div id="system" class="status-list"></div></details>
    </section>
    <section id="view-limits" class="view" hidden>
      <div class="section-head"><div><p class="eyebrow">ПРОВАЙДЕРЫ</p><h2 class="title-with-icon"><span class="title-icon"><svg class="icon" aria-hidden="true"><use href="#icon-gauge"></use></svg></span>Лимиты</h2><p id="limits-summary" class="section-copy muted"></p></div><button id="limits-refresh" class="text-button" type="button"><svg class="icon icon-sm" aria-hidden="true"><use href="#icon-refresh-cw"></use></svg>Обновить</button></div>
      <p id="limits-status" class="runner-status" data-tone="muted" hidden></p>
      <div id="limits-list" class="work-list"></div>
      <section id="limits-errors-section" class="section" hidden><div class="section-head"><div><h2>Ошибки провайдеров</h2></div><span id="limits-errors-count" class="count-pill">0</span></div><div id="limits-errors" class="work-list"></div></section>
    </section>
  </section>
</main>
<button id="quick-add" class="quick-add" type="button" aria-label="Быстро добавить задачу, встречу, напоминание или заметку"><svg class="icon icon-lg" aria-hidden="true"><use href="#icon-plus"></use></svg><span>Добавить</span></button>
<div id="pending-plan" class="pending-plan" hidden><span id="pending-count">0 действий</span><button id="pending-apply" class="primary" type="button">Применить</button><button id="pending-clear" class="text-button" type="button">Сброс</button></div>
<nav id="bottom-nav" class="bottom-nav" aria-label="Навигация"><button class="nav-button is-active" data-view="today" type="button" aria-current="page"><svg class="icon" aria-hidden="true"><use href="#icon-sparkles"></use></svg><span>Сегодня</span></button><button class="nav-button" data-view="tasks" type="button"><svg class="icon" aria-hidden="true"><use href="#icon-list-todo"></use></svg><span>Задачи</span></button><button class="nav-button" data-view="calendar" type="button"><svg class="icon" aria-hidden="true"><use href="#icon-calendar-days"></use></svg><span>План</span></button><button class="nav-button" data-view="money" type="button"><svg class="icon" aria-hidden="true"><use href="#icon-wallet-cards"></use></svg><span>Деньги</span></button><button id="more-nav" class="nav-button" type="button" aria-haspopup="dialog" aria-expanded="false"><svg class="icon" aria-hidden="true"><use href="#icon-layout-grid"></use></svg><span>Ещё</span></button></nav>
<dialog id="more-dialog"><form method="dialog" class="more-sheet"><p class="eyebrow">ЕЩЁ</p><h2>Инструменты JarHert</h2><p class="muted form-help">Код, личная память и состояние AI-провайдеров.</p><div class="more-menu"><button class="more-menu-item" data-more-view="code" type="button"><span class="more-menu-icon warm"><svg class="icon icon-lg" aria-hidden="true"><use href="#icon-code-2"></use></svg></span><span><strong>Код</strong><small>Репозитории, runner и отчёты</small></span><svg class="icon" aria-hidden="true"><use href="#icon-chevron-right"></use></svg></button><button class="more-menu-item" data-more-view="memory" type="button"><span class="more-menu-icon"><svg class="icon icon-lg" aria-hidden="true"><use href="#icon-database"></use></svg></span><span><strong>Память</strong><small>Заметки, обещания и знания</small></span><svg class="icon" aria-hidden="true"><use href="#icon-chevron-right"></use></svg></button><button class="more-menu-item" data-more-view="limits" type="button"><span class="more-menu-icon"><svg class="icon icon-lg" aria-hidden="true"><use href="#icon-gauge"></use></svg></span><span><strong>Лимиты</strong><small>Codex и доступные провайдеры</small></span><svg class="icon" aria-hidden="true"><use href="#icon-chevron-right"></use></svg></button></div><div class="dialog-actions"><button class="primary" type="submit">Закрыть</button></div></form></dialog>
<dialog id="quick-dialog"><form id="quick-form"><p class="eyebrow">ДОБАВИТЬ</p><h2 id="quick-title">Новая задача</h2><div class="quick-types" aria-label="Тип записи"><button class="type-button is-active" data-quick-type="task" type="button"><svg class="icon" aria-hidden="true"><use href="#icon-list-todo"></use></svg>Задача</button><button class="type-button" data-quick-type="event" type="button"><svg class="icon" aria-hidden="true"><use href="#icon-calendar-days"></use></svg>Встреча</button><button class="type-button" data-quick-type="reminder" type="button"><svg class="icon" aria-hidden="true"><use href="#icon-bell"></use></svg>Напомнить</button><button class="type-button" data-quick-type="note" type="button"><svg class="icon" aria-hidden="true"><use href="#icon-sticky-note"></use></svg>Заметка</button></div><label><span id="quick-label">Что сделать</span><textarea id="quick-text" rows="3" maxlength="1000" placeholder="Напиши как есть" required></textarea></label><p id="quick-help" class="muted form-help">Задача попадёт в Inbox без приоритета. Это можно изменить позже.</p><label id="quick-project-field" hidden><span>Проект, если нужен</span><input id="quick-project" type="text" placeholder="Hub_ML" maxlength="120"></label><label id="quick-start-field" hidden><span id="quick-start-label">Когда</span><input id="quick-start" type="datetime-local"></label><label id="quick-end-field" hidden><span>До</span><input id="quick-end" type="datetime-local"></label><div class="dialog-actions"><button id="quick-cancel" class="secondary" type="button">Отмена</button><button class="primary" type="submit">Продолжить<svg class="icon" aria-hidden="true"><use href="#icon-chevron-right"></use></svg></button></div></form></dialog>
<dialog id="coding-dialog"><form id="coding-form"><p class="eyebrow">CODE DESK</p><h2>Поставить кодовую задачу</h2><p class="muted form-help">Один preview перед очередью. Runner не получает секреты и не меняет сервер.</p><label><span>Режим</span><select id="coding-mode"><option value="coding">Разобрать GitHub-репозиторий</option><option value="research">Проверить гипотезу</option></select></label><label><span>Что проверить</span><textarea id="coding-prompt" rows="4" maxlength="3000" placeholder="PDF тупит при перелистывании: найди причину и подготовь фикс с тестами" required></textarea></label><label id="coding-repository-field"><span>GitHub-репозиторий</span><input id="coding-repository" type="url" inputmode="url" placeholder="https://github.com/owner/repo" maxlength="500"></label><label id="coding-sources-field" hidden><span>Ссылки для проверки</span><textarea id="coding-sources" rows="3" maxlength="5000" placeholder="По одной HTTPS ссылке в строке"></textarea></label><div class="dialog-actions"><button id="coding-cancel" class="secondary" type="button">Отмена</button><button class="primary" type="submit">К preview<svg class="icon" aria-hidden="true"><use href="#icon-chevron-right"></use></svg></button></div></form></dialog>
<dialog id="task-menu-dialog"><form method="dialog"><p class="eyebrow">ЗАДАЧА</p><h2 id="task-menu-title">Задача</h2><div class="task-menu-actions"><button id="task-menu-move" class="secondary" type="button"><svg class="icon" aria-hidden="true"><use href="#icon-clock"></use></svg>Перенести</button><button id="task-menu-priority" class="secondary" type="button"><svg class="icon" aria-hidden="true"><use href="#icon-sparkles"></use></svg>Изменить приоритет</button><button id="task-menu-open" class="secondary" type="button"><svg class="icon" aria-hidden="true"><use href="#icon-external-link"></use></svg>Открыть в Trello</button></div><div class="dialog-actions"><button id="task-menu-close" class="primary" type="submit">Готово</button></div></form></dialog>
<dialog id="edit-dialog"><form id="edit-form"><p class="eyebrow" id="edit-eyebrow">КОРРЕКТИРОВКА</p><h2 id="edit-title">Изменить</h2><p id="edit-help" class="muted"></p><label id="edit-field-label"><span id="edit-field-name">Значение</span><input id="edit-date" type="datetime-local" hidden><input id="edit-end" type="datetime-local" hidden><select id="edit-choice" hidden></select><textarea id="edit-value" rows="5"></textarea></label><label id="recurrence-field" hidden><span>Повтор</span><select id="edit-recurrence"><option value="keep">Не менять</option><option value="none">Не повторять</option><option value="daily">Каждый день</option><option value="weekly">Каждую неделю</option><option value="monthly">Каждый месяц</option></select></label><div class="dialog-actions"><button id="dialog-cancel" class="secondary" type="button">Отмена</button><button id="dialog-save" class="primary" type="submit">К preview</button></div></form></dialog>
<dialog id="plan-dialog"><form id="plan-form"><p class="eyebrow">ПРОВЕРЬ И ПОДТВЕРДИ</p><h2>План действий</h2><div id="plan-preview" class="preview-list"></div><p class="muted">Изменения применятся один раз после подтверждения.</p><div class="dialog-actions"><button id="plan-cancel" class="secondary" type="button">Отмена</button><button id="plan-execute" class="primary" type="submit"><svg class="icon" aria-hidden="true"><use href="#icon-check"></use></svg>Применить</button></div></form></dialog>
<dialog id="note-dialog"><form id="note-form"><p class="eyebrow">ПАМЯТЬ</p><h2>Новая заметка</h2><label><span>Тема</span><input id="note-subject" type="text" maxlength="200" placeholder="OAuth, идея, договорённость" required></label><label><span>Текст</span><textarea id="note-content" rows="4" maxlength="4000" placeholder="Суть своими словами" required></textarea></label><label><span>Проект, если нужен</span><input id="note-project" type="text" maxlength="120" placeholder="NoManual"></label><div class="dialog-actions"><button id="note-cancel" class="secondary" type="button">Отмена</button><button class="primary" type="submit">Сохранить</button></div></form></dialog>
<dialog id="reminder-dialog"><form id="reminder-form"><p class="eyebrow">НАПОМИНАНИЕ</p><h2>Новое напоминание</h2><label><span>О чём напомнить</span><input id="reminder-text" type="text" maxlength="500" placeholder="Позвонить врачу" required></label><label><span>Когда</span><input id="reminder-at" type="datetime-local" required></label><label><span>Повтор</span><select id="reminder-recurrence"><option value="none" selected>Не повторять</option><option value="daily">Каждый день</option><option value="weekly">Каждую неделю</option><option value="monthly">Каждый месяц</option></select></label><div class="dialog-actions"><button id="reminder-cancel" class="secondary" type="button">Отмена</button><button class="primary" type="submit">Создать</button></div></form></dialog>
<dialog id="expense-dialog"><form id="expense-form"><p class="eyebrow">ДЕНЬГИ</p><h2>Новая трата</h2><label><span>Сумма</span><input id="expense-amount" type="number" min="0.01" step="0.01" inputmode="decimal" placeholder="1200" required></label><label><span>Валюта</span><select id="expense-currency"><option value="RUB" selected>RUB</option><option value="USD">USD</option><option value="EUR">EUR</option></select></label><label><span>За что</span><input id="expense-text" type="text" maxlength="200" placeholder="AWS, такси, кофе" required></label><label><span>Категория, если нужна</span><input id="expense-category" type="text" maxlength="60" placeholder="infra, food, transport"></label><label><span>Проект, если нужен</span><input id="expense-project" type="text" maxlength="120" placeholder="NoManual"></label><div class="dialog-actions"><button id="expense-cancel" class="secondary" type="button">Отмена</button><button class="primary" type="submit">Записать</button></div></form></dialog>
<dialog id="report-dialog"><form method="dialog"><p class="eyebrow">ОТЧЁТ RUNNER</p><h2 id="report-title">Работа</h2><pre id="report-content" class="report-content"></pre><div class="dialog-actions"><button id="report-close" class="primary" type="submit">Закрыть</button></div></form></dialog>
<dialog id="history-dialog"><form method="dialog"><p class="eyebrow">ИСТОРИЯ ЗАМЕТКИ</p><h2 id="history-title">Заметка</h2><div id="history-content" class="preview-list"></div><div class="dialog-actions"><button id="history-close" class="primary" type="submit">Закрыть</button></div></form></dialog>
<dialog id="architecture-dialog"><form method="dialog" class="architecture-sheet"><p class="eyebrow">ЖИВАЯ КАРТА</p><h2>Как запрос проходит через JarHert</h2><p class="muted">Выбери сценарий: маршрут подсветит, куда уходит запрос и где остаётся твоё решение.</p><div class="architecture-scenarios" role="group" aria-label="Сценарий работы"><button class="architecture-scenario" data-architecture-scenario="question" type="button" aria-pressed="false">Вопрос</button><button class="architecture-scenario is-active" data-architecture-scenario="plan" type="button" aria-pressed="true">Задача</button><button class="architecture-scenario" data-architecture-scenario="voice" type="button" aria-pressed="false">Голос</button><button class="architecture-scenario" data-architecture-scenario="research" type="button" aria-pressed="false">Репа</button></div><section id="architecture-flow-path" class="architecture-flow-path" aria-live="polite"><div class="architecture-flow-head"><p id="architecture-flow-eyebrow" class="eyebrow">СЦЕНАРИЙ · ЗАДАЧА</p><h3 id="architecture-flow-title">От задачи до результата</h3><p id="architecture-flow-summary" class="muted"></p></div><div id="architecture-flow-nodes" class="architecture-flow-nodes" aria-label="Маршрут запроса"></div><article class="architecture-detail"><p id="architecture-detail-eyebrow" class="eyebrow">СЕЙЧАС</p><h4 id="architecture-detail-title"></h4><p id="architecture-detail-copy"></p><p id="architecture-detail-guard" class="muted"></p></article></section><div class="dialog-actions"><button class="primary" type="submit">Понятно</button></div></form></dialog>
<dialog id="clip-dialog"><form id="clip-form"><p class="eyebrow">БАЗА ЗНАНИЙ</p><h2>Сохранить ссылку</h2><p class="muted">Страница не скачивается до твоего preview.</p><label><span>Публичный HTTPS URL</span><input id="clip-url" type="url" inputmode="url" placeholder="https://example.com/article" maxlength="2000" required></label><label><span>Проект, если нужен</span><input id="clip-project" type="text" placeholder="Hub_ML" maxlength="120"></label><div id="clip-preview" class="preview-list"></div><div class="dialog-actions"><button id="clip-cancel" class="secondary" type="button">Отмена</button><button id="clip-preview-action" class="secondary" type="submit">К preview</button><button id="clip-execute" class="primary" type="button" hidden>Сохранить</button></div></form></dialog>
<script src="/assets/dashboard.js?v=__ASSET_VERSION__" defer></script></body></html>""".replace("__ASSET_VERSION__", _asset_version())


app = create_app() if os.getenv("JARHERT_DASHBOARD_AUTOSTART") == "1" else None
