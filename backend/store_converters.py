from __future__ import annotations

from assistant.action_queue import AgentAction, ActionStatus
from assistant.action_schema import ActionType
from assistant.agent_jobs import AgentJob
from assistant.context_store import ConversationTurn
from assistant.delivery_outbox import DeliveryMessage, DeliveryStatus
from assistant.ideas import Idea
from assistant.memory import Memory
from assistant.preferences import UserPreferences
from assistant.provider_router import ProviderFailureKind, ProviderHealth
from backend.models import (
    AgentActionRecord,
    AgentJobRecord,
    ConversationTurnRecord,
    DeliveryOutboxRecord,
    IdeaRecord,
    MemoryRecord,
    ProviderHealthRecord,
    ReminderRecord,
    UserPreferencesRecord,
)
from reminders.store import Reminder, ReminderStatus


def memory_from_record(record: MemoryRecord) -> Memory:
    return Memory(
        id=record.id,
        user_id=record.user_id,
        text=record.text,
        created_at=record.created_at,
    )


def idea_from_record(record: IdeaRecord) -> Idea:
    return Idea(
        id=record.id,
        user_id=record.user_id,
        text=record.text,
        created_at=record.created_at,
    )


def reminder_from_record(
    record: ReminderRecord,
    *,
    status: ReminderStatus | None = None,
) -> Reminder:
    return Reminder(
        id=record.id,
        user_id=record.user_id,
        text=record.text,
        remind_at=record.remind_at,
        status=status or ReminderStatus(record.status),
    )


def agent_job_from_record(record: AgentJobRecord) -> AgentJob:
    return AgentJob(
        id=record.id,
        user_id=record.user_id,
        goal=record.goal,
        status=record.status,
        steps=list(record.steps or []),
        trace_id=record.trace_id or "",
        created_at=record.created_at,
        updated_at=record.updated_at,
        error=record.error,
    )


def agent_action_from_record(record: AgentActionRecord) -> AgentAction:
    return AgentAction(
        id=record.id,
        user_id=record.user_id,
        job_id=record.job_id,
        type=ActionType(record.type),
        payload=dict(record.payload or {}),
        status=ActionStatus(record.status),
        attempts=record.attempts,
        trace_id=record.trace_id or "",
        idempotency_key=record.idempotency_key,
        last_error=record.last_error,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def conversation_turn_from_record(record: ConversationTurnRecord) -> ConversationTurn:
    return ConversationTurn(
        id=record.id,
        user_id=record.user_id,
        user_text=record.user_text,
        assistant_text=record.assistant_text,
        extracted_actions=list(record.extracted_actions or []),
        created_at=record.created_at,
    )


def user_preferences_from_record(record: UserPreferencesRecord) -> UserPreferences:
    return UserPreferences(
        user_id=record.user_id,
        timezone=record.timezone,
        default_trello_list=record.default_trello_list,
        default_project=record.default_project,
        default_reminder_time=record.default_reminder_time,
        morning_time=record.morning_time,
        evening_time=record.evening_time,
        preferred_response_style=record.preferred_response_style,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def provider_health_from_record(record: ProviderHealthRecord) -> ProviderHealth:
    return ProviderHealth(
        name=record.name,
        model=record.model,
        last_success_at=record.last_success_at,
        last_failure_at=record.last_failure_at,
        latency_ms=record.latency_ms,
        auth_error_count=record.auth_error_count,
        rate_limit_count=record.rate_limit_count,
        server_error_count=record.server_error_count,
        timeout_count=record.timeout_count,
        quality_error_count=record.quality_error_count,
        other_error_count=record.other_error_count,
        cooldown_until=record.cooldown_until,
        updated_at=record.updated_at,
    )


def delivery_message_from_record(
    record: DeliveryOutboxRecord,
    *,
    status: DeliveryStatus | None = None,
    attempts: int | None = None,
) -> DeliveryMessage:
    return DeliveryMessage(
        id=record.id,
        user_id=record.user_id,
        chat_id=record.chat_id,
        text=record.text,
        status=status or DeliveryStatus(record.status),
        attempts=record.attempts if attempts is None else attempts,
        trace_id=record.trace_id or "",
        buttons=list(record.buttons or []),
        last_error=record.last_error,
        next_attempt_at=record.next_attempt_at,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def provider_counter_field(failure_kind: ProviderFailureKind) -> str:
    return {
        ProviderFailureKind.AUTH: "auth_error_count",
        ProviderFailureKind.RATE_LIMIT: "rate_limit_count",
        ProviderFailureKind.SERVER_ERROR: "server_error_count",
        ProviderFailureKind.TIMEOUT: "timeout_count",
        ProviderFailureKind.QUALITY: "quality_error_count",
        ProviderFailureKind.OTHER: "other_error_count",
    }[failure_kind]


def truncate_error(error: str, *, limit: int = 1000) -> str:
    value = str(error).strip()
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"
