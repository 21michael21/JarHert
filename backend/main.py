from __future__ import annotations

from contextlib import asynccontextmanager
from backend.config import Settings
from backend.coding_job_store import SqlCodingJobStore
from backend.db import make_session_factory
from backend.migrations import require_current_schema
from backend.user_store import UserStore


settings = Settings()


def get_coding_job_store() -> SqlCodingJobStore:
    return SqlCodingJobStore(make_session_factory(settings.database_url))


def get_or_create_user_id(tg_user_id: int) -> int:
    return UserStore(make_session_factory(settings.database_url)).get_or_create(tg_user_id).id


def health_payload() -> dict[str, str]:
    return {
        "status": "ok",
        "app": settings.app_name,
        "service": "backend",
        "commit": settings.git_commit,
        "built_at": settings.build_time,
        "environment": settings.app_env,
        "hermes_mode": settings.hermes_mode,
    }


try:
    from fastapi import FastAPI, Header, HTTPException
    from pydantic import BaseModel, Field

    from gateway_bot.main import get_gateway_service

    class TelegramTextRequest(BaseModel):
        tg_user_id: int = Field(gt=0)
        text: str = Field(min_length=1, max_length=8000)

    class AssistantTextResponse(BaseModel):
        text: str
        intent: str
        provider: str | None = None
        model: str | None = None
        fallback_count: int = 0
        blocked_reason: str | None = None

    class CodingJobCreateRequest(BaseModel):
        tg_user_id: int = Field(gt=0)
        mode: str = Field(pattern="^(coding|research)$")
        prompt: str = Field(min_length=1, max_length=5000)
        repository_url: str | None = Field(default=None, max_length=500)
        source_urls: list[str] = Field(default_factory=list, max_length=10)
        idempotency_key: str | None = Field(default=None, max_length=180)

    class CodingJobClaimRequest(BaseModel):
        worker_id: str = Field(min_length=1, max_length=100)
        lease_seconds: int = Field(default=1200, ge=60, le=3600)

    class CodingJobHeartbeatRequest(BaseModel):
        worker_id: str = Field(min_length=1, max_length=100)
        lease_seconds: int = Field(default=1200, ge=60, le=3600)

    class CodingJobCompleteRequest(BaseModel):
        worker_id: str = Field(min_length=1, max_length=100)
        result_text: str = Field(min_length=1, max_length=20000)

    class CodingJobFailRequest(BaseModel):
        worker_id: str = Field(min_length=1, max_length=100)
        error: str = Field(min_length=1, max_length=500)

    @asynccontextmanager
    async def lifespan(_app):
        require_current_schema(settings.database_url)
        yield

    app = FastAPI(title=settings.app_name, lifespan=lifespan)

    def _service_token_authorized(
        *,
        authorization: str | None,
        x_assistant_service_token: str | None,
        configured_token: str,
    ) -> bool:
        if not configured_token:
            return False
        if x_assistant_service_token == configured_token:
            return True
        prefix = "Bearer "
        return bool(authorization and authorization.startswith(prefix) and authorization[len(prefix) :] == configured_token)

    def _require_service_token(authorization: str | None, x_assistant_service_token: str | None) -> None:
        if not _service_token_authorized(
            authorization=authorization,
            x_assistant_service_token=x_assistant_service_token,
            configured_token=settings.assistant_service_token,
        ):
            raise HTTPException(status_code=401, detail="assistant service token required")

    @app.get("/health")
    def health() -> dict[str, str]:
        return health_payload()

    @app.get("/readyz")
    def readiness() -> dict[str, object]:
        checks: dict[str, str] = {}
        try:
            require_current_schema(settings.database_url)
        except Exception as error:
            checks["schema"] = "fail"
            raise HTTPException(status_code=503, detail="database schema is not ready") from error
        checks["schema"] = "ok"
        return {**health_payload(), "status": "ready", "checks": checks}

    @app.get("/api/version")
    def version() -> dict[str, str]:
        return health_payload()

    @app.post("/api/assistant/telegram-text")
    def telegram_text(
        payload: TelegramTextRequest,
        authorization: str | None = Header(default=None),
        x_assistant_service_token: str | None = Header(default=None),
    ) -> AssistantTextResponse:
        _require_service_token(authorization, x_assistant_service_token)

        reply = get_gateway_service().handle_text(payload.tg_user_id, payload.text)
        return AssistantTextResponse(
            text=reply.text,
            intent=reply.intent.value,
            provider=reply.provider,
            model=reply.model,
            fallback_count=reply.fallback_count,
            blocked_reason=reply.blocked_reason,
        )

    @app.post("/api/coding/jobs")
    def coding_job_create(
        payload: CodingJobCreateRequest,
        authorization: str | None = Header(default=None),
        x_assistant_service_token: str | None = Header(default=None),
    ) -> dict[str, object]:
        _require_service_token(authorization, x_assistant_service_token)
        job = get_coding_job_store().enqueue(
            user_id=get_or_create_user_id(payload.tg_user_id),
            mode=payload.mode,
            prompt=payload.prompt,
            repository_url=payload.repository_url,
            source_urls=payload.source_urls,
            idempotency_key=payload.idempotency_key,
        )
        return _coding_payload(job)

    @app.post("/api/coding/jobs/claim")
    def coding_job_claim(
        payload: CodingJobClaimRequest,
        authorization: str | None = Header(default=None),
        x_assistant_service_token: str | None = Header(default=None),
    ) -> dict[str, object] | None:
        _require_service_token(authorization, x_assistant_service_token)
        job = get_coding_job_store().claim_next(
            worker_id=payload.worker_id,
            lease_seconds=payload.lease_seconds,
        )
        return _coding_payload(job) if job is not None else None

    @app.post("/api/coding/jobs/{job_id}/heartbeat")
    def coding_job_heartbeat(
        job_id: int,
        payload: CodingJobHeartbeatRequest,
        authorization: str | None = Header(default=None),
        x_assistant_service_token: str | None = Header(default=None),
    ) -> dict[str, bool]:
        _require_service_token(authorization, x_assistant_service_token)
        return {"ok": get_coding_job_store().heartbeat(
            job_id, worker_id=payload.worker_id, lease_seconds=payload.lease_seconds
        )}

    @app.post("/api/coding/jobs/{job_id}/complete")
    def coding_job_complete(
        job_id: int,
        payload: CodingJobCompleteRequest,
        authorization: str | None = Header(default=None),
        x_assistant_service_token: str | None = Header(default=None),
    ) -> dict[str, object]:
        _require_service_token(authorization, x_assistant_service_token)
        return _coding_payload(get_coding_job_store().complete(
            job_id, worker_id=payload.worker_id, result_text=payload.result_text
        ))

    @app.post("/api/coding/jobs/{job_id}/fail")
    def coding_job_fail(
        job_id: int,
        payload: CodingJobFailRequest,
        authorization: str | None = Header(default=None),
        x_assistant_service_token: str | None = Header(default=None),
    ) -> dict[str, object]:
        _require_service_token(authorization, x_assistant_service_token)
        return _coding_payload(get_coding_job_store().fail(
            job_id, worker_id=payload.worker_id, error=payload.error
        ))

    def _coding_payload(job) -> dict[str, object]:
        return {
            name: getattr(job, name, None)
            for name in (
                "id", "user_id", "mode", "prompt", "repository_url", "source_urls",
                "status", "idempotency_key", "worker_id", "lease_until", "result_text",
                "last_error", "created_at", "updated_at",
            )
        }
except ModuleNotFoundError:
    app = None
