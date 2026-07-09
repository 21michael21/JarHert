from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timezone

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from assistant.action_queue import AgentAction, ActionStatus
from assistant.action_schema import ActionType
from assistant.agent_jobs import AgentJob
from assistant.context_store import ConversationTurn
from assistant.delivery_outbox import DeliveryMessage, DeliveryStatus
from assistant.limits import DailyLimitStore
from assistant.ideas import Idea
from assistant.memory import Memory
from assistant.preferences import UserPreferences
from assistant.provider_router import ProviderFailureKind, ProviderHealth
from backend.models import (
    AgentActionRecord,
    AgentJobRecord,
    ConversationTurnRecord,
    DeliveryOutboxRecord,
    Event,
    IdeaRecord,
    MemoryRecord,
    ProviderHealthRecord,
    ReminderRecord,
    UsageDaily,
    User,
    UserPreferencesRecord,
)
from reminders.store import Reminder, ReminderStatus


class UserStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def get_or_create(self, tg_user_id: int) -> User:
        with self.session_factory() as db:
            user = db.scalar(select(User).where(User.tg_user_id == tg_user_id))
            if user is not None:
                return user
            user = User(tg_user_id=tg_user_id)
            db.add(user)
            db.commit()
            db.refresh(user)
            return user


class SqlMemoryStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def add(self, user_id: int, text: str) -> Memory:
        with self.session_factory() as db:
            record = MemoryRecord(user_id=user_id, text=text.strip())
            db.add(record)
            db.commit()
            db.refresh(record)
            return _memory_from_record(record)

    def list_for_user(self, user_id: int, *, limit: int = 10) -> list[Memory]:
        with self.session_factory() as db:
            records = db.scalars(
                select(MemoryRecord)
                .where(MemoryRecord.user_id == user_id)
                .order_by(MemoryRecord.created_at.desc(), MemoryRecord.id.desc())
                .limit(limit)
            ).all()
            return [_memory_from_record(record) for record in records]


class SqlIdeaStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def add(self, user_id: int, text: str) -> Idea:
        with self.session_factory() as db:
            record = IdeaRecord(user_id=user_id, text=text.strip())
            db.add(record)
            db.commit()
            db.refresh(record)
            return _idea_from_record(record)

    def list_for_user(self, user_id: int, *, limit: int = 10) -> list[Idea]:
        with self.session_factory() as db:
            records = db.scalars(
                select(IdeaRecord)
                .where(IdeaRecord.user_id == user_id)
                .order_by(IdeaRecord.created_at.desc(), IdeaRecord.id.desc())
                .limit(limit)
            ).all()
            return [_idea_from_record(record) for record in records]


class SqlReminderStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def add(self, user_id: int, text: str, remind_at: datetime) -> Reminder:
        with self.session_factory() as db:
            record = ReminderRecord(
                user_id=user_id,
                text=text.strip(),
                remind_at=remind_at,
                status=ReminderStatus.PENDING.value,
            )
            db.add(record)
            db.commit()
            db.refresh(record)
            return _reminder_from_record(record)

    def list_pending_for_user(self, user_id: int, *, limit: int = 10) -> list[Reminder]:
        with self.session_factory() as db:
            records = db.scalars(
                select(ReminderRecord)
                .where(
                    ReminderRecord.user_id == user_id,
                    ReminderRecord.status == ReminderStatus.PENDING.value,
                )
                .order_by(ReminderRecord.remind_at.asc(), ReminderRecord.id.asc())
                .limit(limit)
            ).all()
            return [_reminder_from_record(record) for record in records]

    def claim_due(self, *, now: datetime | None = None, limit: int = 20) -> list[Reminder]:
        due_at = now or datetime.now(timezone.utc)
        with self.session_factory() as db:
            records = db.scalars(
                select(ReminderRecord)
                .where(
                    ReminderRecord.status == ReminderStatus.PENDING.value,
                    ReminderRecord.remind_at <= due_at,
                )
                .order_by(ReminderRecord.remind_at.asc(), ReminderRecord.id.asc())
                .limit(limit)
            ).all()
            ids = [record.id for record in records]
            if ids:
                db.execute(
                    update(ReminderRecord)
                    .where(ReminderRecord.id.in_(ids))
                    .values(
                        status=ReminderStatus.SENDING.value,
                        attempts=ReminderRecord.attempts + 1,
                    )
                )
                db.commit()
            return [_reminder_from_record(record, status=ReminderStatus.SENDING) for record in records]

    def mark_sent(self, reminder_id: int, *, sent_at: datetime | None = None) -> None:
        value = sent_at or datetime.now(timezone.utc)
        with self.session_factory() as db:
            db.execute(
                update(ReminderRecord)
                .where(ReminderRecord.id == reminder_id)
                .values(status=ReminderStatus.SENT.value, sent_at=value)
            )
            db.commit()

    def release_for_retry(self, reminder_id: int, *, max_attempts: int = 3) -> None:
        with self.session_factory() as db:
            record = db.get(ReminderRecord, reminder_id)
            if record is None:
                return
            record.status = (
                ReminderStatus.FAILED.value
                if record.attempts >= max_attempts
                else ReminderStatus.PENDING.value
            )
            db.commit()

    def cancel_for_user(self, user_id: int, reminder_id: int) -> bool:
        with self.session_factory() as db:
            record = db.scalar(
                select(ReminderRecord).where(
                    ReminderRecord.id == reminder_id,
                    ReminderRecord.user_id == user_id,
                    ReminderRecord.status == ReminderStatus.PENDING.value,
                )
            )
            if record is None:
                return False
            record.status = ReminderStatus.CANCELLED.value
            db.commit()
            return True


class SqlAgentJobStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def create(self, user_id: int, goal: str, steps: list[str]) -> AgentJob:
        with self.session_factory() as db:
            record = AgentJobRecord(
                user_id=user_id,
                goal=goal.strip(),
                status="queued",
                steps=list(steps),
            )
            db.add(record)
            db.commit()
            db.refresh(record)
            return _agent_job_from_record(record)

    def list_for_user(self, user_id: int, *, limit: int = 10) -> list[AgentJob]:
        with self.session_factory() as db:
            records = db.scalars(
                select(AgentJobRecord)
                .where(AgentJobRecord.user_id == user_id)
                .order_by(AgentJobRecord.created_at.desc(), AgentJobRecord.id.desc())
                .limit(limit)
            ).all()
            return [_agent_job_from_record(record) for record in records]

    def get_for_user(self, user_id: int, job_id: int) -> AgentJob | None:
        with self.session_factory() as db:
            record = db.scalar(
                select(AgentJobRecord).where(
                    AgentJobRecord.id == job_id,
                    AgentJobRecord.user_id == user_id,
                )
            )
            if record is None:
                return None
            return _agent_job_from_record(record)


class SqlActionQueueStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def enqueue(
        self,
        *,
        user_id: int,
        action_type: ActionType,
        payload: dict[str, str],
        job_id: int | None = None,
        idempotency_key: str | None = None,
        status: ActionStatus = ActionStatus.QUEUED,
    ) -> AgentAction:
        with self.session_factory() as db:
            if idempotency_key:
                existing = db.scalar(
                    select(AgentActionRecord).where(
                        AgentActionRecord.user_id == user_id,
                        AgentActionRecord.idempotency_key == idempotency_key,
                    )
                )
                if existing is not None:
                    return _agent_action_from_record(existing)
            record = AgentActionRecord(
                user_id=user_id,
                job_id=job_id,
                type=action_type.value,
                payload=dict(payload),
                status=status.value,
                idempotency_key=idempotency_key,
            )
            db.add(record)
            db.commit()
            db.refresh(record)
            return _agent_action_from_record(record)

    def list_for_user(self, user_id: int, *, limit: int = 20) -> list[AgentAction]:
        with self.session_factory() as db:
            records = db.scalars(
                select(AgentActionRecord)
                .where(AgentActionRecord.user_id == user_id)
                .order_by(AgentActionRecord.created_at.desc(), AgentActionRecord.id.desc())
                .limit(limit)
            ).all()
            return [_agent_action_from_record(record) for record in records]

    def claim_next(self) -> AgentAction | None:
        with self.session_factory() as db:
            record = db.scalar(
                select(AgentActionRecord)
                .where(AgentActionRecord.status == ActionStatus.QUEUED.value)
                .order_by(AgentActionRecord.created_at.asc(), AgentActionRecord.id.asc())
                .limit(1)
            )
            if record is None:
                return None
            record.status = ActionStatus.RUNNING.value
            record.attempts += 1
            db.commit()
            db.refresh(record)
            return _agent_action_from_record(record)

    def mark_succeeded(self, action_id: int) -> AgentAction:
        with self.session_factory() as db:
            record = _require_agent_action(db, action_id)
            record.status = ActionStatus.SUCCEEDED.value
            record.last_error = None
            db.commit()
            db.refresh(record)
            return _agent_action_from_record(record)

    def mark_failed(self, action_id: int, error: str) -> AgentAction:
        with self.session_factory() as db:
            record = _require_agent_action(db, action_id)
            record.status = ActionStatus.FAILED.value
            record.last_error = _truncate_error(error)
            db.commit()
            db.refresh(record)
            return _agent_action_from_record(record)

    def retry_failed(self, action_id: int) -> AgentAction:
        with self.session_factory() as db:
            record = _require_agent_action(db, action_id)
            record.status = ActionStatus.QUEUED.value
            db.commit()
            db.refresh(record)
            return _agent_action_from_record(record)

    def cancel_for_user(self, user_id: int, action_id: int) -> bool:
        with self.session_factory() as db:
            record = db.scalar(
                select(AgentActionRecord).where(
                    AgentActionRecord.id == action_id,
                    AgentActionRecord.user_id == user_id,
                    AgentActionRecord.status.in_(
                        [ActionStatus.QUEUED.value, ActionStatus.NEEDS_CONFIRMATION.value]
                    ),
                )
            )
            if record is None:
                return False
            record.status = ActionStatus.CANCELLED.value
            db.commit()
            return True


class SqlConversationStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def add(
        self,
        *,
        user_id: int,
        user_text: str,
        assistant_text: str,
        extracted_actions: list[dict] | None = None,
    ) -> ConversationTurn:
        with self.session_factory() as db:
            record = ConversationTurnRecord(
                user_id=user_id,
                user_text=(user_text or "").strip(),
                assistant_text=(assistant_text or "").strip(),
                extracted_actions=list(extracted_actions or []),
            )
            db.add(record)
            db.commit()
            db.refresh(record)
            return _conversation_turn_from_record(record)

    def list_recent(self, user_id: int, *, limit: int = 10) -> list[ConversationTurn]:
        with self.session_factory() as db:
            records = db.scalars(
                select(ConversationTurnRecord)
                .where(ConversationTurnRecord.user_id == user_id)
                .order_by(ConversationTurnRecord.created_at.desc(), ConversationTurnRecord.id.desc())
                .limit(limit)
            ).all()
            return [_conversation_turn_from_record(record) for record in records]

    def latest_user_text(self, user_id: int) -> str | None:
        for turn in self.list_recent(user_id, limit=10):
            value = (turn.user_text or "").strip()
            if value and value.lower() not in {
                "запиши это как идею",
                "сохрани это как идею",
                "запиши это как важное",
                "сохрани это как важное",
            }:
                return value
        return None


class SqlUserPreferenceStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def get(self, user_id: int) -> UserPreferences:
        with self.session_factory() as db:
            record = db.scalar(select(UserPreferencesRecord).where(UserPreferencesRecord.user_id == user_id))
            if record is None:
                record = UserPreferencesRecord(user_id=user_id)
                db.add(record)
                db.commit()
                db.refresh(record)
            return _user_preferences_from_record(record)

    def update(self, user_id: int, **updates) -> UserPreferences:
        allowed = {
            "timezone",
            "default_trello_list",
            "default_project",
            "default_reminder_time",
            "morning_time",
            "evening_time",
            "preferred_response_style",
        }
        values = {key: value for key, value in updates.items() if key in allowed}
        with self.session_factory() as db:
            record = db.scalar(select(UserPreferencesRecord).where(UserPreferencesRecord.user_id == user_id))
            if record is None:
                record = UserPreferencesRecord(user_id=user_id)
                db.add(record)
                db.flush()
            for key, value in values.items():
                setattr(record, key, value)
            db.commit()
            db.refresh(record)
            return _user_preferences_from_record(record)


class SqlProviderHealthStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def get(self, name: str) -> ProviderHealth:
        with self.session_factory() as db:
            record = db.scalar(select(ProviderHealthRecord).where(ProviderHealthRecord.name == name))
            if record is None:
                return ProviderHealth(name=name)
            return _provider_health_from_record(record)

    def list_all(self) -> list[ProviderHealth]:
        with self.session_factory() as db:
            records = db.scalars(
                select(ProviderHealthRecord).order_by(ProviderHealthRecord.id.asc())
            ).all()
            return [_provider_health_from_record(record) for record in records]

    def record_success(self, name: str, model: str, *, latency_ms: int | None = None) -> ProviderHealth:
        with self.session_factory() as db:
            record = _get_or_create_provider_health(db, name)
            record.model = model
            record.last_success_at = datetime.now(timezone.utc)
            record.latency_ms = latency_ms
            record.cooldown_until = None
            db.commit()
            db.refresh(record)
            return _provider_health_from_record(record)

    def record_failure(
        self,
        name: str,
        model: str,
        failure_kind: ProviderFailureKind,
        *,
        latency_ms: int | None = None,
        cooldown_until: datetime | None = None,
    ) -> ProviderHealth:
        with self.session_factory() as db:
            record = _get_or_create_provider_health(db, name)
            record.model = model
            record.last_failure_at = datetime.now(timezone.utc)
            record.latency_ms = latency_ms
            record.cooldown_until = cooldown_until
            counter = _provider_counter_field(failure_kind)
            setattr(record, counter, getattr(record, counter) + 1)
            db.commit()
            db.refresh(record)
            return _provider_health_from_record(record)


class SqlDeliveryOutboxStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def enqueue(
        self,
        *,
        user_id: int,
        chat_id: int,
        text: str,
        next_attempt_at: datetime | None = None,
    ) -> DeliveryMessage:
        with self.session_factory() as db:
            record = DeliveryOutboxRecord(
                user_id=user_id,
                chat_id=chat_id,
                text=text.strip(),
                status=DeliveryStatus.QUEUED.value,
                next_attempt_at=next_attempt_at,
            )
            db.add(record)
            db.commit()
            db.refresh(record)
            return _delivery_message_from_record(record)

    def claim_due(
        self,
        *,
        now: datetime | None = None,
        limit: int = 20,
    ) -> list[DeliveryMessage]:
        due_at = now or datetime.now(timezone.utc)
        with self.session_factory() as db:
            records = db.scalars(
                select(DeliveryOutboxRecord)
                .where(
                    DeliveryOutboxRecord.status == DeliveryStatus.QUEUED.value,
                    (
                        DeliveryOutboxRecord.next_attempt_at.is_(None)
                        | (DeliveryOutboxRecord.next_attempt_at <= due_at)
                    ),
                )
                .order_by(DeliveryOutboxRecord.created_at.asc(), DeliveryOutboxRecord.id.asc())
                .limit(limit)
            ).all()
            ids = [record.id for record in records]
            if ids:
                db.execute(
                    update(DeliveryOutboxRecord)
                    .where(DeliveryOutboxRecord.id.in_(ids))
                    .values(
                        status=DeliveryStatus.SENDING.value,
                        attempts=DeliveryOutboxRecord.attempts + 1,
                    )
                )
                db.commit()
            return [_delivery_message_from_record(record, status=DeliveryStatus.SENDING) for record in records]

    def mark_sent(self, message_id: int) -> DeliveryMessage:
        with self.session_factory() as db:
            record = _require_delivery_message(db, message_id)
            record.status = DeliveryStatus.SENT.value
            record.last_error = None
            record.next_attempt_at = None
            db.commit()
            db.refresh(record)
            return _delivery_message_from_record(record)

    def mark_retry(self, message_id: int, error: str, next_attempt_at: datetime) -> DeliveryMessage:
        with self.session_factory() as db:
            record = _require_delivery_message(db, message_id)
            record.status = DeliveryStatus.QUEUED.value
            record.last_error = _truncate_error(error)
            record.next_attempt_at = next_attempt_at
            db.commit()
            db.refresh(record)
            return _delivery_message_from_record(record)

    def mark_failed_permanent(self, message_id: int, error: str) -> DeliveryMessage:
        with self.session_factory() as db:
            record = _require_delivery_message(db, message_id)
            record.status = DeliveryStatus.FAILED.value
            record.last_error = _truncate_error(error)
            record.next_attempt_at = None
            db.commit()
            db.refresh(record)
            return _delivery_message_from_record(record)

    def list_recent(self, *, limit: int = 20) -> list[DeliveryMessage]:
        with self.session_factory() as db:
            records = db.scalars(
                select(DeliveryOutboxRecord)
                .order_by(DeliveryOutboxRecord.created_at.desc(), DeliveryOutboxRecord.id.desc())
                .limit(limit)
            ).all()
            return [_delivery_message_from_record(record) for record in records]

    def stats(self) -> dict[str, int]:
        with self.session_factory() as db:
            rows = db.execute(
                select(DeliveryOutboxRecord.status, func.count(DeliveryOutboxRecord.id)).group_by(
                    DeliveryOutboxRecord.status
                )
            ).all()
        counts = {status: count for status, count in rows}
        return {status.value: counts.get(status.value, 0) for status in DeliveryStatus}


class SqlDailyLimitStore(DailyLimitStore):
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        per_user_limit: int = 20,
        global_limit: int = 200,
    ) -> None:
        super().__init__(per_user_limit=per_user_limit, global_limit=global_limit)
        self.session_factory = session_factory

    def can_consume(self, user_id: int, *, today: date | None = None) -> bool:
        day = today or date.today()
        day_key = day.isoformat()
        with self.session_factory() as db:
            user_count = self._request_count(db, user_id, day_key)
            global_count = db.scalar(select(func.coalesce(func.sum(UsageDaily.request_count), 0)).where(UsageDaily.day == day_key))
            return user_count < self.per_user_limit and (global_count or 0) < self.global_limit

    def consume(self, user_id: int, *, today: date | None = None) -> bool:
        day = today or date.today()
        day_key = day.isoformat()
        with self.session_factory() as db:
            record = db.scalar(
                select(UsageDaily).where(
                    UsageDaily.user_id == user_id,
                    UsageDaily.day == day_key,
                )
            )
            if record is None:
                record = UsageDaily(user_id=user_id, day=day_key, request_count=0)
                db.add(record)
                db.flush()
            global_count = db.scalar(select(func.coalesce(func.sum(UsageDaily.request_count), 0)).where(UsageDaily.day == day_key)) or 0
            if record.request_count >= self.per_user_limit or global_count >= self.global_limit:
                db.rollback()
                return False
            record.request_count += 1
            db.commit()
            return True

    def remaining_for_user(self, user_id: int, *, today: date | None = None) -> int:
        day = today or date.today()
        with self.session_factory() as db:
            used = self._request_count(db, user_id, day.isoformat())
        return max(0, self.per_user_limit - used)

    @staticmethod
    def _request_count(db: Session, user_id: int, day: str) -> int:
        return (
            db.scalar(
                select(UsageDaily.request_count).where(
                    UsageDaily.user_id == user_id,
                    UsageDaily.day == day,
                )
            )
            or 0
        )


class EventStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def log(self, user_id: int, event_type: str, meta: dict | None = None) -> None:
        with self.session_factory() as db:
            db.add(Event(user_id=user_id, type=event_type, meta=meta))
            db.commit()

    def log_assistant_response(
        self,
        user_id: int,
        event_type: str,
        *,
        intent: str,
        blocked_reason: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        fallback_count: int = 0,
        perf_ms: dict[str, int] | None = None,
    ) -> None:
        self.log(
            user_id,
            event_type,
            {
                "intent": intent,
                "blocked_reason": blocked_reason,
                "provider": provider,
                "model": model,
                "fallback_count": fallback_count,
                "perf_ms": dict(perf_ms or {}),
            },
        )


def _memory_from_record(record: MemoryRecord) -> Memory:
    return Memory(
        id=record.id,
        user_id=record.user_id,
        text=record.text,
        created_at=record.created_at,
    )


def _idea_from_record(record: IdeaRecord) -> Idea:
    return Idea(
        id=record.id,
        user_id=record.user_id,
        text=record.text,
        created_at=record.created_at,
    )


def _reminder_from_record(
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


def _agent_job_from_record(record: AgentJobRecord) -> AgentJob:
    return AgentJob(
        id=record.id,
        user_id=record.user_id,
        goal=record.goal,
        status=record.status,
        steps=list(record.steps or []),
        created_at=record.created_at,
        updated_at=record.updated_at,
        error=record.error,
    )


def _agent_action_from_record(record: AgentActionRecord) -> AgentAction:
    return AgentAction(
        id=record.id,
        user_id=record.user_id,
        job_id=record.job_id,
        type=ActionType(record.type),
        payload=dict(record.payload or {}),
        status=ActionStatus(record.status),
        attempts=record.attempts,
        idempotency_key=record.idempotency_key,
        last_error=record.last_error,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _conversation_turn_from_record(record: ConversationTurnRecord) -> ConversationTurn:
    return ConversationTurn(
        id=record.id,
        user_id=record.user_id,
        user_text=record.user_text,
        assistant_text=record.assistant_text,
        extracted_actions=list(record.extracted_actions or []),
        created_at=record.created_at,
    )


def _user_preferences_from_record(record: UserPreferencesRecord) -> UserPreferences:
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


def _provider_health_from_record(record: ProviderHealthRecord) -> ProviderHealth:
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


def _delivery_message_from_record(
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
        last_error=record.last_error,
        next_attempt_at=record.next_attempt_at,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _get_or_create_provider_health(db: Session, name: str) -> ProviderHealthRecord:
    record = db.scalar(select(ProviderHealthRecord).where(ProviderHealthRecord.name == name))
    if record is None:
        record = ProviderHealthRecord(name=name)
        db.add(record)
        db.flush()
    return record


def _provider_counter_field(failure_kind: ProviderFailureKind) -> str:
    return {
        ProviderFailureKind.AUTH: "auth_error_count",
        ProviderFailureKind.RATE_LIMIT: "rate_limit_count",
        ProviderFailureKind.SERVER_ERROR: "server_error_count",
        ProviderFailureKind.TIMEOUT: "timeout_count",
        ProviderFailureKind.QUALITY: "quality_error_count",
        ProviderFailureKind.OTHER: "other_error_count",
    }[failure_kind]


def _require_agent_action(db: Session, action_id: int) -> AgentActionRecord:
    record = db.get(AgentActionRecord, action_id)
    if record is None:
        raise KeyError(f"action not found: {action_id}")
    return record


def _require_delivery_message(db: Session, message_id: int) -> DeliveryOutboxRecord:
    record = db.get(DeliveryOutboxRecord, message_id)
    if record is None:
        raise KeyError(f"delivery message not found: {message_id}")
    return record


def _truncate_error(error: str, *, limit: int = 1000) -> str:
    value = str(error).strip()
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


ReminderSender = Callable[[Reminder], None]
