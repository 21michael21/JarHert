from __future__ import annotations

from dataclasses import dataclass

from assistant.action_queue import ActionStatus
from assistant.pipeline import AssistantPipeline
from assistant.types import AssistantReply, Intent, ReplyButton, UserContext
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
                trace_id=reply.trace_id,
            )
        return reply

    def confirm_action(self, tg_user_id: int, action_id: int) -> AssistantReply:
        user = self._user_context(tg_user_id)
        if user is None:
            return _not_allowed_reply()
        if self.pipeline.action_queue is None:
            return AssistantReply(text="Очередь действий не подключена.", intent=Intent.AGENT_DO, blocked_reason="queue_disabled")
        action = self.pipeline.action_queue.confirm_for_user(user.user_id, action_id)
        if action is None:
            return AssistantReply(
                text=f"Не нашёл действие #{action_id}, которое ждёт подтверждения.",
                intent=Intent.AGENT_DO,
                blocked_reason="action_not_confirmable",
            )
        if self.events is not None:
            self.events.log(
                user.user_id,
                "action_confirmed",
                {"action_id": action.id, "job_id": action.job_id, "type": action.type.value},
                trace_id=action.trace_id,
            )
        return AssistantReply(
            text=f"Подтвердил action #{action.id}. Выполняю в очереди.",
            intent=Intent.AGENT_DO,
            trace_id=action.trace_id,
            buttons=_status_buttons(action.job_id),
        )

    def cancel_action(self, tg_user_id: int, action_id: int) -> AssistantReply:
        user = self._user_context(tg_user_id)
        if user is None:
            return _not_allowed_reply()
        if self.pipeline.action_queue is None:
            return AssistantReply(text="Очередь действий не подключена.", intent=Intent.AGENT_DO, blocked_reason="queue_disabled")
        action = next(
            (
                item
                for item in self.pipeline.action_queue.list_for_user(user.user_id, limit=100)
                if item.id == action_id
            ),
            None,
        )
        cancelled = self.pipeline.action_queue.cancel_for_user(user.user_id, action_id)
        if not cancelled:
            return AssistantReply(
                text=f"Не нашёл действие #{action_id}, которое можно отменить.",
                intent=Intent.AGENT_DO,
                blocked_reason="action_not_cancellable",
            )
        trace_id = action.trace_id if action is not None else ""
        if self.events is not None:
            self.events.log(
                user.user_id,
                "action_cancelled",
                {"action_id": action_id, "job_id": action.job_id if action is not None else None},
                trace_id=trace_id,
            )
        return AssistantReply(text=f"Отменил action #{action_id}.", intent=Intent.AGENT_DO, trace_id=trace_id)

    def job_status(self, tg_user_id: int, job_id: int) -> AssistantReply:
        user = self._user_context(tg_user_id)
        if user is None:
            return _not_allowed_reply()
        job = self.pipeline.agent_jobs.get_for_user(user.user_id, job_id)
        if job is None:
            return AssistantReply(
                text=f"Не нашёл job #{job_id}.",
                intent=Intent.AGENT_JOB,
                blocked_reason="agent_job_not_found",
            )
        actions = []
        if self.pipeline.action_queue is not None:
            actions = [
                action
                for action in self.pipeline.action_queue.list_for_user(user.user_id, limit=100)
                if action.job_id == job.id
            ]
        lines = [f"Job #{job.id}", f"Статус: {job.status}", f"Trace: {job.trace_id or 'none'}"]
        if actions:
            lines.append("Actions:")
            lines.extend(f"{action.id}. {action.type.value} — {action.status.value}" for action in actions)
        buttons = []
        for action in actions:
            if action.status == ActionStatus.NEEDS_CONFIRMATION:
                buttons.append(
                    [
                        ReplyButton("Подтвердить", f"ai:confirm:{action.id}"),
                        ReplyButton("Отменить", f"ai:cancel:{action.id}"),
                    ]
                )
        return AssistantReply(text="\n".join(lines), intent=Intent.AGENT_JOB, trace_id=job.trace_id, buttons=buttons)

    def _user_context(self, tg_user_id: int) -> UserContext | None:
        if not self.is_allowed(tg_user_id):
            return None
        db_user = self.users.get_or_create(tg_user_id) if self.users is not None else None
        user_id = db_user.id if db_user is not None else tg_user_id
        return UserContext(
            user_id=user_id,
            tg_user_id=tg_user_id,
            is_admin=tg_user_id in (self.admin_tg_user_ids or set()),
        )


def _status_buttons(job_id: int | None) -> list[list[ReplyButton]]:
    if job_id is None:
        return []
    return [[ReplyButton("Статус job", f"ai:status:{job_id}")]]


def _not_allowed_reply() -> AssistantReply:
    return AssistantReply(
        text="Этот бот пока закрыт. Попроси владельца добавить твой Telegram ID в allowlist.",
        intent=Intent.UNKNOWN,
        blocked_reason="user_not_allowed",
    )
