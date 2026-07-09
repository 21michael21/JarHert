from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column

from backend.db import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    tg_user_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class AutomationWorkerLeaseRecord(Base):
    __tablename__ = "automation_worker_leases"
    __table_args__ = (
        Index("ix_automation_worker_status_next_run", "status", "next_run_at"),
        Index("ix_automation_worker_lease_until", "lease_until"),
    )

    worker_name: Mapped[str] = mapped_column(String(100), primary_key=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="idle", server_default="idle")
    owner_id: Mapped[str | None] = mapped_column(String(100))
    generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    failure_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class MemoryRecord(Base):
    __tablename__ = "memories"
    __table_args__ = (Index("ix_memories_user_created", "user_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class IdeaRecord(Base):
    __tablename__ = "ideas"
    __table_args__ = (Index("ix_ideas_user_created", "user_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class ReminderRecord(Base):
    __tablename__ = "reminders"
    __table_args__ = (Index("ix_reminders_status_due", "status", "remind_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    remind_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AgentJobRecord(Base):
    __tablename__ = "agent_jobs"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key", name="uq_agent_jobs_user_idempotency"),
        Index("ix_agent_jobs_user_status_created", "user_id", "status", "created_at"),
        Index("ix_agent_jobs_trace", "trace_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    goal: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    steps: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    trace_id: Mapped[str | None] = mapped_column(String(40))
    idempotency_key: Mapped[str | None] = mapped_column(String(180))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class AgentActionRecord(Base):
    __tablename__ = "agent_actions"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key", name="uq_agent_actions_user_idempotency"),
        Index("ix_agent_actions_status_created", "status", "created_at"),
        Index("ix_agent_actions_user_status_created", "user_id", "status", "created_at"),
        Index("ix_agent_actions_trace", "trace_id"),
        Index("ix_agent_actions_status_lease", "status", "lease_until"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("agent_jobs.id", ondelete="SET NULL"))
    type: Mapped[str] = mapped_column(String(80), nullable=False)
    payload: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="queued")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    trace_id: Mapped[str | None] = mapped_column(String(40))
    depends_on_action_id: Mapped[int | None] = mapped_column(Integer)
    compensation_for_action_id: Mapped[int | None] = mapped_column(Integer)
    compensation_status: Mapped[str] = mapped_column(String(30), nullable=False, default="none")
    result_meta: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False, default=dict, server_default=text("'{}'"))
    result_text: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str | None] = mapped_column(String(180))
    worker_id: Mapped[str | None] = mapped_column(String(100))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class ConversationTurnRecord(Base):
    __tablename__ = "conversation_turns"
    __table_args__ = (Index("ix_conversation_turns_user_created", "user_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    user_text: Mapped[str] = mapped_column(Text, nullable=False)
    assistant_text: Mapped[str] = mapped_column(Text, nullable=False)
    extracted_actions: Mapped[list[dict]] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class UserPreferencesRecord(Base):
    __tablename__ = "user_preferences"
    __table_args__ = (UniqueConstraint("user_id", name="uq_user_preferences_user"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    timezone: Mapped[str] = mapped_column(String(80), nullable=False, default="UTC")
    default_trello_list: Mapped[str] = mapped_column(String(120), nullable=False, default="Inbox")
    default_project: Mapped[str | None] = mapped_column(String(120))
    default_reminder_time: Mapped[str] = mapped_column(String(5), nullable=False, default="09:00")
    morning_time: Mapped[str] = mapped_column(String(5), nullable=False, default="09:00")
    evening_time: Mapped[str] = mapped_column(String(5), nullable=False, default="19:00")
    preferred_response_style: Mapped[str] = mapped_column(String(30), nullable=False, default="concise")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class ProviderHealthRecord(Base):
    __tablename__ = "provider_health"
    __table_args__ = (Index("ix_provider_health_updated", "updated_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    model: Mapped[str] = mapped_column(String(180), nullable=False, default="")
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_failure_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    auth_error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rate_limit_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    server_error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    timeout_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    quality_error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    other_error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    quality_score: Mapped[int] = mapped_column(Integer, nullable=False, default=100, server_default="100")
    quality_sample_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    cooldown_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class ProviderBudgetDailyRecord(Base):
    __tablename__ = "provider_budget_daily"

    id: Mapped[int] = mapped_column(primary_key=True)
    day: Mapped[str] = mapped_column(String(10), unique=True, nullable=False)
    estimated_cost_micro_usd: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    request_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class ProviderBudgetEntryRecord(Base):
    __tablename__ = "provider_budget_entries"
    __table_args__ = (Index("ix_provider_budget_entries_day_provider", "day", "provider_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    day: Mapped[str] = mapped_column(String(10), nullable=False)
    provider_name: Mapped[str] = mapped_column(String(120), nullable=False)
    estimated_cost_micro_usd: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class DeliveryOutboxRecord(Base):
    __tablename__ = "delivery_outbox"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key", name="uq_delivery_outbox_user_idempotency"),
        Index("ix_delivery_outbox_status_next_attempt", "status", "next_attempt_at"),
        Index("ix_delivery_outbox_user_status_created", "user_id", "status", "created_at"),
        Index("ix_delivery_outbox_trace", "trace_id"),
        Index("ix_delivery_outbox_status_lease", "status", "lease_until"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    trace_id: Mapped[str | None] = mapped_column(String(40))
    idempotency_key: Mapped[str | None] = mapped_column(String(180))
    buttons: Mapped[list[dict] | None] = mapped_column(JSON)
    worker_id: Mapped[str | None] = mapped_column(String(100))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class InboundUpdateRecord(Base):
    __tablename__ = "inbound_updates"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key", name="uq_inbound_updates_user_idempotency"),
        Index("ix_inbound_updates_status_updated", "status", "updated_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(220), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="processing")
    response: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict, server_default=text("'{}'"))
    trace_id: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class MonitorJobRecord(Base):
    __tablename__ = "monitor_jobs"
    __table_args__ = (
        Index("ix_monitor_jobs_enabled_created", "enabled", "created_at"),
        Index("ix_monitor_jobs_user_enabled", "user_id", "enabled"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_type: Mapped[str] = mapped_column(String(80), nullable=False)
    source_config: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False, default=dict, server_default=text("'{}'"))
    condition_text: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("1"))
    last_state_hash: Mapped[str | None] = mapped_column(String(64))
    last_payload: Mapped[dict | None] = mapped_column(JSON)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class MonitorRunRecord(Base):
    __tablename__ = "monitor_runs"
    __table_args__ = (Index("ix_monitor_runs_job_created", "monitor_job_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    monitor_job_id: Mapped[int] = mapped_column(ForeignKey("monitor_jobs.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    triggered: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("0"))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class CollectedMessageRecord(Base):
    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("chat_id", "telegram_message_id", name="uq_messages_chat_message"),
        Index("ix_messages_processed_timestamp", "is_processed", "timestamp"),
        Index("ix_messages_chat_timestamp", "chat_id", "timestamp"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_message_id: Mapped[int | None] = mapped_column(Integer)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    chat_title: Mapped[str | None] = mapped_column(String(250))
    sender_id: Mapped[int | None] = mapped_column(BigInteger)
    sender_name: Mapped[str | None] = mapped_column(String(250))
    text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_processed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class UsageDaily(Base):
    __tablename__ = "usage_daily"
    __table_args__ = (UniqueConstraint("user_id", "day", name="uq_usage_daily_user_day"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    day: Mapped[str] = mapped_column(String(10), nullable=False)
    request_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (
        Index("ix_events_user_type_created", "user_id", "type", "created_at"),
        Index("ix_events_trace", "trace_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    type: Mapped[str] = mapped_column(String(80), nullable=False)
    trace_id: Mapped[str | None] = mapped_column(String(40))
    meta: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
