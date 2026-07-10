from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timezone

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from assistant.context_store import ConversationTurn
from assistant.ideas import Idea
from assistant.limits import DailyLimitStore
from assistant.memory import Memory
from assistant.preferences import UserPreferences
from backend.models import (
    ConversationTurnRecord,
    ReminderRecord,
    UsageDaily,
    UserPreferencesRecord,
)
from backend.personal_knowledge_store import SqlPersonalKnowledgeStore
from backend.store_converters import (
    conversation_turn_from_record,
    reminder_from_record,
    user_preferences_from_record,
)
from reminders.store import Reminder, ReminderStatus


class SqlMemoryStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory
        self.knowledge = SqlPersonalKnowledgeStore(session_factory)

    def add(self, user_id: int, text: str) -> Memory:
        note = self.knowledge.create(
            user_id=user_id,
            text=text,
            note_type="memory",
            source="legacy_memory",
        )
        return Memory(
            id=note.id,
            user_id=note.user_id,
            text=note.text,
            created_at=note.created_at,
        )

    def list_for_user(self, user_id: int, *, limit: int = 10) -> list[Memory]:
        return [
            Memory(id=note.id, user_id=note.user_id, text=note.text, created_at=note.created_at)
            for note in self.knowledge.list_for_user(user_id, limit=limit, note_type="memory")
        ]


class SqlIdeaStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory
        self.knowledge = SqlPersonalKnowledgeStore(session_factory)

    def add(self, user_id: int, text: str) -> Idea:
        note = self.knowledge.create(
            user_id=user_id,
            text=text,
            note_type="idea",
            source="legacy_idea",
        )
        return Idea(
            id=note.id,
            user_id=note.user_id,
            text=note.text,
            created_at=note.created_at,
        )

    def list_for_user(self, user_id: int, *, limit: int = 10) -> list[Idea]:
        return [
            Idea(id=note.id, user_id=note.user_id, text=note.text, created_at=note.created_at)
            for note in self.knowledge.list_for_user(user_id, limit=limit, note_type="idea")
        ]


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
            return reminder_from_record(record)

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
            return [reminder_from_record(record) for record in records]

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
            return [reminder_from_record(record, status=ReminderStatus.SENDING) for record in records]

    def recover_sending(self, *, max_attempts: int = 3) -> int:
        with self.session_factory() as db:
            sending = db.scalars(
                select(ReminderRecord).where(ReminderRecord.status == ReminderStatus.SENDING.value)
            ).all()
            for record in sending:
                record.status = (
                    ReminderStatus.FAILED.value
                    if record.attempts >= max_attempts
                    else ReminderStatus.PENDING.value
                )
            db.commit()
            return len(sending)

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
            return conversation_turn_from_record(record)

    def list_recent(self, user_id: int, *, limit: int = 10) -> list[ConversationTurn]:
        with self.session_factory() as db:
            records = db.scalars(
                select(ConversationTurnRecord)
                .where(ConversationTurnRecord.user_id == user_id)
                .order_by(ConversationTurnRecord.created_at.desc(), ConversationTurnRecord.id.desc())
                .limit(limit)
            ).all()
            return [conversation_turn_from_record(record) for record in records]

    def get_for_user(self, user_id: int, turn_id: int) -> ConversationTurn | None:
        with self.session_factory() as db:
            record = db.scalar(
                select(ConversationTurnRecord).where(
                    ConversationTurnRecord.id == turn_id,
                    ConversationTurnRecord.user_id == user_id,
                )
            )
            return conversation_turn_from_record(record) if record is not None else None

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
            return user_preferences_from_record(record)

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
            return user_preferences_from_record(record)


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


ReminderSender = Callable[[Reminder], None]
