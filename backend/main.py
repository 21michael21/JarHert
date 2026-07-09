from __future__ import annotations

from backend.config import Settings


settings = Settings()


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

    app = FastAPI(title=settings.app_name)

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

    @app.get("/health")
    def health() -> dict[str, str]:
        return health_payload()

    @app.get("/api/version")
    def version() -> dict[str, str]:
        return health_payload()

    @app.post("/api/assistant/telegram-text")
    def telegram_text(
        payload: TelegramTextRequest,
        authorization: str | None = Header(default=None),
        x_assistant_service_token: str | None = Header(default=None),
    ) -> AssistantTextResponse:
        if not _service_token_authorized(
            authorization=authorization,
            x_assistant_service_token=x_assistant_service_token,
            configured_token=settings.assistant_service_token,
        ):
            raise HTTPException(status_code=401, detail="assistant service token required")

        reply = get_gateway_service().handle_text(payload.tg_user_id, payload.text)
        return AssistantTextResponse(
            text=reply.text,
            intent=reply.intent.value,
            provider=reply.provider,
            model=reply.model,
            fallback_count=reply.fallback_count,
            blocked_reason=reply.blocked_reason,
        )
except ModuleNotFoundError:
    app = None
