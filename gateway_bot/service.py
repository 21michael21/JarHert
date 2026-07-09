from __future__ import annotations

from dataclasses import dataclass

from assistant.pipeline import AssistantPipeline
from assistant.types import AssistantReply, Intent, UserContext
from backend.stores import EventStore, UserStore


@dataclass
class GatewayService:
    pipeline: AssistantPipeline
    allowed_tg_user_ids: set[int] | None = None
    admin_tg_user_ids: set[int] | None = None
    users: UserStore | None = None
    events: EventStore | None = None

    def is_allowed(self, tg_user_id: int) -> bool:
        if not self.allowed_tg_user_ids:
            return True
        return tg_user_id in self.allowed_tg_user_ids

    def handle_text(self, tg_user_id: int, text: str) -> AssistantReply:
        if not self.is_allowed(tg_user_id):
            return AssistantReply(
                text="Этот бот пока закрыт. Попроси владельца добавить твой Telegram ID в allowlist.",
                intent=Intent.UNKNOWN,
                blocked_reason="user_not_allowed",
            )
        db_user = self.users.get_or_create(tg_user_id) if self.users is not None else None
        user_id = db_user.id if db_user is not None else tg_user_id
        user = UserContext(
            user_id=user_id,
            tg_user_id=tg_user_id,
            is_admin=tg_user_id in (self.admin_tg_user_ids or set()),
        )
        reply = self.pipeline.handle_text(user, text)
        if self.events is not None:
            self.events.log_assistant_response(
                user_id,
                f"assistant_{reply.intent.value}",
                intent=reply.intent.value,
                blocked_reason=reply.blocked_reason,
                provider=reply.provider,
                model=reply.model,
                fallback_count=reply.fallback_count,
                perf_ms=reply.perf_ms,
            )
        return reply
