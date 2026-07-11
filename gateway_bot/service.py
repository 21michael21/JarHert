from __future__ import annotations

from dataclasses import dataclass

from assistant.admin_status_service import build_perf_status_text
from assistant.action_queue import ActionStatus
from assistant.input_router import UnifiedInput, normalize_input_text
from assistant.job_orchestration import compute_job_status
from assistant.pipeline import AssistantPipeline
from assistant.tool_result_ids import compact_result_meta
from assistant.tracing import new_trace_id
from assistant.training_feedback import TrainingExampleType, classify_training_example_type
from assistant.types import AssistantReply, Intent, ReplyButton, UserContext
from backend.stores import EventStore, UserStore
from backend.trace_store import SqlTraceStore, TraceSnapshot
from backend.training_feedback_store import SqlTrainingFeedbackStore


@dataclass
class GatewayService:
    pipeline: AssistantPipeline
    allowed_tg_user_ids: set[int] | None = None
    admin_tg_user_ids: set[int] | None = None
    users: UserStore | None = None
    events: EventStore | None = None
    traces: SqlTraceStore | None = None
    inbound_updates: object | None = None
    training_feedback: SqlTrainingFeedbackStore | None = None
    training_feedback_buttons_enabled: bool = False
    personal_exports: object | None = None

    def is_allowed(self, tg_user_id: int) -> bool:
        if not self.allowed_tg_user_ids:
            return True
        return tg_user_id in self.allowed_tg_user_ids

    def handle_text(
        self,
        tg_user_id: int,
        text: str,
        *,
        idempotency_key: str = "",
        trace_id: str = "",
    ) -> AssistantReply:
        if not self.is_allowed(tg_user_id):
            return AssistantReply(
                text="Этот бот пока закрыт. Попроси владельца добавить твой Telegram ID в allowlist.",
                intent=Intent.UNKNOWN,
                blocked_reason="user_not_allowed",
            )
        trace_text = (text or "").strip()
        if trace_text == "/trace" or trace_text.startswith("/trace "):
            return self.trace_status(tg_user_id, trace_text.partition(" ")[2].strip())
        db_user = self.users.get_or_create(tg_user_id) if self.users is not None else None
        user_id = db_user.id if db_user is not None else tg_user_id
        trace_id = trace_id or new_trace_id()
        user = UserContext(
            user_id=user_id,
            tg_user_id=tg_user_id,
            is_admin=tg_user_id in (self.admin_tg_user_ids or set()),
        )
        if trace_text == "/admin_status perf":
            return self.perf_status(user)
        if idempotency_key and self.inbound_updates is not None:
            claim = self.inbound_updates.claim(user_id, idempotency_key, trace_id=trace_id)
            if not claim.acquired:
                if claim.status == "completed" and claim.response:
                    return _reply_from_payload(claim.response)
                return AssistantReply(
                    text="",
                    intent=Intent.UNKNOWN,
                    blocked_reason="duplicate_update_in_progress",
                    trace_id=claim.trace_id,
                    suppress_delivery=True,
                )
        if self.events is not None:
            self.events.log(
                user_id,
                "telegram_update_received",
                {"source": "telegram", "idempotent": bool(idempotency_key)},
                trace_id=trace_id,
            )
        captured_feedback = self._capture_pending_feedback(user, trace_text, trace_id=trace_id)
        if captured_feedback is not None:
            if idempotency_key and self.inbound_updates is not None:
                self.inbound_updates.complete(
                    user_id,
                    idempotency_key,
                    _reply_to_payload(captured_feedback),
                    trace_id=captured_feedback.trace_id,
                )
            return captured_feedback
        try:
            reply = self.pipeline.handle_text(user, text, idempotency_key=idempotency_key, trace_id=trace_id)
        except Exception:
            if idempotency_key and self.inbound_updates is not None:
                self.inbound_updates.mark_failed(user_id, idempotency_key)
            raise
        reply = self._attach_training_feedback_buttons(reply)
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
        if idempotency_key and self.inbound_updates is not None:
            self.inbound_updates.complete(
                user_id,
                idempotency_key,
                _reply_to_payload(reply),
                trace_id=reply.trace_id,
            )
        return reply

    def approve_training_reply(self, tg_user_id: int, turn_id: int) -> AssistantReply:
        user, turn = self._training_turn_for_user(tg_user_id, turn_id)
        if user is None:
            return _not_allowed_reply()
        if turn is None or self.training_feedback is None:
            return AssistantReply(
                text="Не нашёл этот ответ для обучения.",
                intent=Intent.STATUS,
                blocked_reason="training_turn_not_found",
            )
        example = self.training_feedback.approve_turn(user.user_id, turn)
        self._log_training_event(user.user_id, "training_example_approved", example.id)
        return AssistantReply(text="Сохранил согласованную пару для локального набора.", intent=Intent.STATUS)

    def edit_training_reply(self, tg_user_id: int, turn_id: int) -> AssistantReply:
        user, turn = self._training_turn_for_user(tg_user_id, turn_id)
        if user is None:
            return _not_allowed_reply()
        if turn is None or self.training_feedback is None:
            return AssistantReply(
                text="Не нашёл этот ответ для обучения.",
                intent=Intent.STATUS,
                blocked_reason="training_turn_not_found",
            )
        example = self.training_feedback.begin_edit(user.user_id, turn)
        if example.status.value == "approved":
            return AssistantReply(text="Эта пара уже сохранена. Выбери другой ответ, если хочешь его исправить.", intent=Intent.STATUS)
        self._log_training_event(user.user_id, "training_example_edit_requested", example.id)
        return AssistantReply(
            text="Пришли следующей репликой исправленную версию ответа. Сохраню только очищенную согласованную пару.",
            intent=Intent.STATUS,
        )

    def shorten_training_reply(self, tg_user_id: int, turn_id: int, *, trace_id: str = "") -> AssistantReply:
        user, turn = self._training_turn_for_user(tg_user_id, turn_id)
        if user is None:
            return _not_allowed_reply()
        if turn is None:
            return AssistantReply(
                text="Не нашёл этот ответ для сокращения.",
                intent=Intent.STATUS,
                blocked_reason="training_turn_not_found",
            )
        reply = self.pipeline.rewrite_shorter(user, turn, trace_id=trace_id)
        return self._attach_training_feedback_buttons(reply)

    def handle_input(
        self,
        tg_user_id: int,
        inbound: UnifiedInput,
        *,
        idempotency_key: str = "",
        trace_id: str = "",
    ) -> AssistantReply:
        text = normalize_input_text(inbound)
        if not text:
            return AssistantReply(
                text="Что сделать с этим файлом или сообщением?",
                intent=Intent.UNKNOWN,
                blocked_reason="input_needs_clarification",
                trace_id=trace_id,
            )
        return self.handle_text(tg_user_id, text, idempotency_key=idempotency_key, trace_id=trace_id)

    def create_personal_export(self, tg_user_id: int):
        if not self.is_allowed(tg_user_id):
            raise PermissionError("user not allowed")
        if self.personal_exports is None or self.users is None:
            raise RuntimeError("personal export is not configured")
        user = self.users.get_or_create(tg_user_id)
        return self.personal_exports.create(user_id=user.id, tg_user_id=tg_user_id)

    def perf_status(self, user: UserContext) -> AssistantReply:
        if not user.is_admin:
            return AssistantReply(
                text="Эта команда доступна только владельцу бота.",
                intent=Intent.ADMIN_STATUS,
                blocked_reason="admin_required",
            )
        return AssistantReply(
            text=build_perf_status_text(self.events),
            intent=Intent.ADMIN_STATUS,
        )

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
            suppress_delivery=True,
        )

    def confirm_job(self, tg_user_id: int, job_id: int) -> AssistantReply:
        user = self._user_context(tg_user_id)
        if user is None:
            return _not_allowed_reply()
        if self.pipeline.action_queue is None:
            return AssistantReply(text="Очередь действий не подключена.", intent=Intent.AGENT_DO, blocked_reason="queue_disabled")
        job = self.pipeline.agent_jobs.get_for_user(user.user_id, job_id)
        if job is None:
            return AssistantReply(
                text=f"Не нашёл job #{job_id}.",
                intent=Intent.AGENT_JOB,
                blocked_reason="agent_job_not_found",
            )
        confirmed = self.pipeline.action_queue.confirm_job_for_user(user.user_id, job_id)
        if not confirmed:
            return AssistantReply(
                text=f"В Job #{job_id} нет действий, которые ждут подтверждения.",
                intent=Intent.AGENT_JOB,
                trace_id=job.trace_id,
                blocked_reason="job_not_confirmable",
            )
        if self.events is not None:
            self.events.log(
                user.user_id,
                "job_confirmed",
                {"job_id": job_id, "action_count": len(confirmed)},
                trace_id=job.trace_id,
            )
            for action in confirmed:
                self.events.log(
                    user.user_id,
                    "action_confirmed",
                    {"action_id": action.id, "job_id": job_id, "type": action.type.value},
                    trace_id=action.trace_id,
                )
        return AssistantReply(
            text=f"Подтвердил Job #{job_id}: {len(confirmed)} действий. Выполняю по порядку.",
            intent=Intent.AGENT_DO,
            trace_id=job.trace_id,
            suppress_delivery=True,
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

    def cancel_job(self, tg_user_id: int, job_id: int) -> AssistantReply:
        user = self._user_context(tg_user_id)
        if user is None:
            return _not_allowed_reply()
        if self.pipeline.action_queue is None:
            return AssistantReply(text="Очередь действий не подключена.", intent=Intent.AGENT_DO, blocked_reason="queue_disabled")
        job = self.pipeline.agent_jobs.get_for_user(user.user_id, job_id)
        if job is None:
            return AssistantReply(
                text=f"Не нашёл job #{job_id}.",
                intent=Intent.AGENT_JOB,
                blocked_reason="agent_job_not_found",
            )
        cancelled = self.pipeline.action_queue.cancel_job_for_user(user.user_id, job_id)
        if not cancelled:
            return AssistantReply(
                text=f"В Job #{job_id} нет действий, которые можно отменить.",
                intent=Intent.AGENT_JOB,
                trace_id=job.trace_id,
                blocked_reason="job_not_cancellable",
            )
        self.pipeline.agent_jobs.mark_status(job_id, "cancelled")
        if self.events is not None:
            self.events.log(
                user.user_id,
                "job_cancelled",
                {"job_id": job_id, "action_count": len(cancelled)},
                trace_id=job.trace_id,
            )
            for action in cancelled:
                self.events.log(
                    user.user_id,
                    "action_cancelled",
                    {"action_id": action.id, "job_id": job_id, "type": action.type.value},
                    trace_id=action.trace_id,
                )
        return AssistantReply(
            text=f"Отменил Job #{job_id}: {len(cancelled)} действий.",
            intent=Intent.AGENT_DO,
            trace_id=job.trace_id,
        )

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
        summary = compute_job_status(actions)
        stored_status = job.status if job.status == summary.status else f"{job.status} computed={summary.status}"
        lines = [
            f"Job #{job.id}",
            f"Статус: {stored_status}",
            f"Прогресс: {summary.progress_text}",
            f"Trace: {job.trace_id or 'none'}",
        ]
        if summary.next_action_id is not None:
            lines.append(f"Следующее действие: action #{summary.next_action_id}")
        if summary.compensation_available:
            lines.append(f"Компенсация: {summary.compensation_available} шаг(ов) готовы к rollback по external ids.")
        if summary.compensation_not_supported:
            lines.append(f"Компенсация: {summary.compensation_not_supported} шаг(ов) требуют ручной проверки.")
        if actions:
            lines.append("Actions:")
            for action in sorted(actions, key=lambda item: (item.created_at, item.id)):
                dependency = f" after #{action.depends_on_action_id}" if action.depends_on_action_id else ""
                compensation = (
                    f" compensation={action.compensation_status}"
                    if action.compensation_status != "none"
                    else ""
                )
                ids = f" ids={compact_result_meta(action.result_meta)}" if action.result_meta else ""
                lines.append(
                    f"{action.id}. {action.type.value} — {action.status.value}{dependency}{compensation}{ids}"
                )
                if action.status == ActionStatus.SUCCEEDED and action.result_text:
                    lines.append(f"   checkpoint: {action.result_text[:160]}")
        buttons = []
        if any(action.status == ActionStatus.NEEDS_CONFIRMATION for action in actions):
            buttons.append(
                [
                    ReplyButton("Подтвердить всё", f"ai:confirm_job:{job.id}"),
                    ReplyButton("Отменить всё", f"ai:cancel_job:{job.id}"),
                ]
            )
        elif job.status == "paused":
            buttons.append(
                [
                    ReplyButton("Продолжить", f"ai:resume_job:{job.id}"),
                    ReplyButton("Отменить", f"ai:cancel_job:{job.id}"),
                ]
            )
        elif any(action.status in {ActionStatus.QUEUED, ActionStatus.RUNNING} for action in actions):
            buttons.append(
                [
                    ReplyButton("Пауза", f"ai:pause_job:{job.id}"),
                    ReplyButton("Отменить", f"ai:cancel_job:{job.id}"),
                ]
            )
        return AssistantReply(text="\n".join(lines), intent=Intent.AGENT_JOB, trace_id=job.trace_id, buttons=buttons)

    def pause_job(self, tg_user_id: int, job_id: int) -> AssistantReply:
        return self.handle_text(tg_user_id, f"/job pause {job_id}")

    def resume_job(self, tg_user_id: int, job_id: int) -> AssistantReply:
        return self.handle_text(tg_user_id, f"/job resume {job_id}")

    def trace_status(self, tg_user_id: int, trace_id: str) -> AssistantReply:
        user = self._user_context(tg_user_id)
        if user is None:
            return _not_allowed_reply()
        if not user.is_admin:
            return AssistantReply(
                text="Эта команда доступна только владельцу бота.",
                intent=Intent.TRACE,
                blocked_reason="admin_required",
            )
        clean_trace_id = (trace_id or "").strip()
        if not clean_trace_id:
            return AssistantReply(
                text="Укажи trace id: /trace abc123",
                intent=Intent.TRACE,
                blocked_reason="trace_id_required",
            )
        if self.traces is None:
            return AssistantReply(
                text="Trace viewer не подключён.",
                intent=Intent.TRACE,
                blocked_reason="trace_store_disabled",
            )
        snapshot = self.traces.get(clean_trace_id)
        if snapshot.empty:
            return AssistantReply(
                text=f"Trace {clean_trace_id}: ничего не найдено.",
                intent=Intent.TRACE,
                blocked_reason="trace_not_found",
            )
        return AssistantReply(text=_format_trace(snapshot), intent=Intent.TRACE, trace_id=clean_trace_id)

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

    def _training_turn_for_user(self, tg_user_id: int, turn_id: int):
        user = self._user_context(tg_user_id)
        if user is None:
            return None, None
        get_turn = getattr(self.pipeline.conversation_turns, "get_for_user", None)
        turn = get_turn(user.user_id, turn_id) if callable(get_turn) else None
        return user, turn

    def _capture_pending_feedback(self, user: UserContext, text: str, *, trace_id: str) -> AssistantReply | None:
        if self.training_feedback is None or not text.strip():
            return None
        example = self.training_feedback.consume_pending_edit(user.user_id, text)
        if example is None:
            return None
        self._log_training_event(user.user_id, "training_example_edited", example.id, trace_id=trace_id)
        return AssistantReply(
            text="Сохранил согласованную пару для локального набора.",
            intent=Intent.STATUS,
            trace_id=trace_id,
        )

    def _attach_training_feedback_buttons(self, reply: AssistantReply) -> AssistantReply:
        response_type = classify_training_example_type("", reply.text)
        if (
            self.training_feedback is None
            or not self.training_feedback_buttons_enabled
            or reply.intent is not Intent.ASK
            or (reply.blocked_reason is not None and response_type is not TrainingExampleType.SAFE_REFUSAL)
            or reply.suppress_delivery
            or reply.conversation_turn_id is None
            or reply.buttons
        ):
            return reply
        turn_id = reply.conversation_turn_id
        return AssistantReply(
            text=reply.text,
            intent=reply.intent,
            provider=reply.provider,
            model=reply.model,
            fallback_count=reply.fallback_count,
            blocked_reason=reply.blocked_reason,
            perf_ms=reply.perf_ms,
            trace_id=reply.trace_id,
            buttons=[
                [ReplyButton("Нормально", f"ai:feedback_ok:{turn_id}")],
                [ReplyButton("Сделай короче", f"ai:feedback_shorter:{turn_id}")],
                [ReplyButton("Я исправил сам", f"ai:feedback_edit:{turn_id}")],
            ],
            conversation_turn_id=turn_id,
        )

    def _log_training_event(self, user_id: int, event_type: str, example_id: int, *, trace_id: str = "") -> None:
        if self.events is not None:
            self.events.log(user_id, event_type, {"example_id": example_id}, trace_id=trace_id)


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


def _format_trace(snapshot: TraceSnapshot) -> str:
    lines = [f"Trace {snapshot.trace_id}"]
    if snapshot.jobs:
        lines.append("Jobs:")
        for job in snapshot.jobs[:8]:
            computed_status = _computed_job_status(job.id, snapshot)
            status = job.status if computed_status == job.status else f"{job.status} computed={computed_status}"
            lines.append(f"- #{job.id} {status}: goal=<redacted:{job.goal_length} chars>")
    if snapshot.actions:
        lines.append("Actions:")
        for action in snapshot.actions[:12]:
            dependency = f" after={action.depends_on_action_id}" if action.depends_on_action_id else ""
            compensation = (
                f" compensation={action.compensation_status}"
                if action.compensation_status != "none"
                else ""
            )
            ids = f" ids={compact_result_meta(action.result_meta)}" if action.result_meta else ""
            lines.append(
                f"- #{action.id} job={action.job_id or '-'} {action.type} "
                f"{action.status} attempts={action.attempts}{dependency}{compensation}{ids}"
            )
    if snapshot.deliveries:
        lines.append("Delivery:")
        for delivery in snapshot.deliveries[:12]:
            lines.append(f"- #{delivery.id} {delivery.status} attempts={delivery.attempts}")
    if snapshot.events:
        lines.append("Events:")
        for event in snapshot.events[:20]:
            meta = _event_meta_summary(event.meta or {})
            suffix = f" {meta}" if meta else ""
            lines.append(f"- #{event.id} {event.type}{suffix}")
    return "\n".join(lines)


def _event_meta_summary(meta: dict) -> str:
    allowed_keys = (
        "intent",
        "blocked_reason",
        "provider",
        "model",
        "fallback_count",
        "job_id",
        "action_id",
        "delivery_id",
        "type",
        "retryable",
        "attempts",
        "status",
        "progress",
        "blocked",
        "failed",
        "depends_on_action_id",
        "blocked_by_action_id",
        "failed_action_id",
        "result_meta",
        "delivery_id",
        "queue_lag_ms",
        "delivery_latency_ms",
        "latency_ms",
        "worker",
        "owner_id",
        "generation",
        "recovered",
        "recovered_items",
        "delay_seconds",
        "error_type",
        "has_buttons",
    )
    parts = []
    for key in allowed_keys:
        value = meta.get(key)
        if value in (None, "", {}):
            continue
        parts.append(f"{key}={_compact(str(value), limit=60)}")
    return " ".join(parts)


def _computed_job_status(job_id: int, snapshot: TraceSnapshot) -> str:
    from assistant.action_queue import AgentAction
    from assistant.action_schema import ActionType

    actions = [
        AgentAction(
            id=action.id,
            user_id=action.user_id,
            job_id=action.job_id,
            type=ActionType(action.type),
            payload={},
            status=ActionStatus(action.status),
            attempts=action.attempts,
            last_error=action.last_error,
            created_at=action.created_at,
            depends_on_action_id=action.depends_on_action_id,
            compensation_status=action.compensation_status,
            result_meta=action.result_meta,
        )
        for action in snapshot.actions
        if action.job_id == job_id
    ]
    if not actions:
        return "unknown"
    return compute_job_status(actions).status


def _compact(value: str | None, *, limit: int = 120) -> str:
    clean = " ".join((value or "").split())
    if len(clean) <= limit:
        return clean
    return clean[: limit - 1].rstrip() + "…"


def _reply_to_payload(reply: AssistantReply) -> dict:
    return {
        "text": reply.text,
        "intent": reply.intent.value,
        "provider": reply.provider,
        "model": reply.model,
        "fallback_count": reply.fallback_count,
        "blocked_reason": reply.blocked_reason,
        "perf_ms": dict(reply.perf_ms),
        "trace_id": reply.trace_id,
        "conversation_turn_id": reply.conversation_turn_id,
        "buttons": [
            [
                {"text": button.text, "callback_data": button.callback_data}
                for button in row
            ]
            for row in reply.buttons
        ],
    }


def _reply_from_payload(payload: dict) -> AssistantReply:
    return AssistantReply(
        text=str(payload.get("text") or ""),
        intent=Intent(str(payload.get("intent") or Intent.UNKNOWN.value)),
        provider=payload.get("provider"),
        model=payload.get("model"),
        fallback_count=int(payload.get("fallback_count") or 0),
        blocked_reason=payload.get("blocked_reason"),
        perf_ms=dict(payload.get("perf_ms") or {}),
        trace_id=str(payload.get("trace_id") or ""),
        conversation_turn_id=(
            int(payload["conversation_turn_id"])
            if payload.get("conversation_turn_id") is not None
            else None
        ),
        buttons=[
            [
                ReplyButton(
                    text=str(button.get("text") or ""),
                    callback_data=str(button.get("callback_data") or ""),
                )
                for button in row
            ]
            for row in payload.get("buttons") or []
        ],
    )
