from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from assistant.context_store import ConversationTurn
from assistant.training_data import redact_training_text
from assistant.training_feedback import TrainingExample, TrainingFeedbackKind, TrainingFeedbackStatus
from backend.models import TrainingExampleRecord


class SqlTrainingFeedbackStore:
    """Stores only explicitly approved, redacted pairs for later local curation."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def approve_turn(self, user_id: int, turn: ConversationTurn) -> TrainingExample:
        return self._create_or_get(
            user_id=user_id,
            turn=turn,
            feedback_kind=TrainingFeedbackKind.NORMAL,
            status=TrainingFeedbackStatus.APPROVED,
            assistant_text=turn.assistant_text,
            approved_at=datetime.now(timezone.utc),
        )

    def begin_edit(self, user_id: int, turn: ConversationTurn) -> TrainingExample:
        with self.session_factory() as db:
            existing = db.scalar(
                select(TrainingExampleRecord).where(
                    TrainingExampleRecord.user_id == user_id,
                    TrainingExampleRecord.conversation_turn_id == turn.id,
                )
            )
            if existing is not None:
                return training_example_from_record(existing)
            db.execute(
                delete(TrainingExampleRecord).where(
                    TrainingExampleRecord.user_id == user_id,
                    TrainingExampleRecord.status == TrainingFeedbackStatus.PENDING_EDIT.value,
                )
            )
            db.commit()
        return self._create_or_get(
            user_id=user_id,
            turn=turn,
            feedback_kind=TrainingFeedbackKind.EDIT,
            status=TrainingFeedbackStatus.PENDING_EDIT,
            assistant_text=None,
            approved_at=None,
        )

    def consume_pending_edit(self, user_id: int, assistant_text: str) -> TrainingExample | None:
        clean_text, _ = redact_training_text(assistant_text)
        if not clean_text:
            return None
        with self.session_factory() as db:
            record = db.scalar(
                select(TrainingExampleRecord)
                .where(
                    TrainingExampleRecord.user_id == user_id,
                    TrainingExampleRecord.status == TrainingFeedbackStatus.PENDING_EDIT.value,
                )
                .order_by(TrainingExampleRecord.created_at.desc(), TrainingExampleRecord.id.desc())
                .limit(1)
            )
            if record is None:
                return None
            record.assistant_text = clean_text
            record.status = TrainingFeedbackStatus.APPROVED.value
            record.approved_at = datetime.now(timezone.utc)
            db.commit()
            db.refresh(record)
            return training_example_from_record(record)

    def list_approved(self, user_id: int, *, limit: int = 400) -> list[TrainingExample]:
        with self.session_factory() as db:
            records = db.scalars(
                select(TrainingExampleRecord)
                .where(
                    TrainingExampleRecord.user_id == user_id,
                    TrainingExampleRecord.status == TrainingFeedbackStatus.APPROVED.value,
                )
                .order_by(TrainingExampleRecord.approved_at.asc(), TrainingExampleRecord.id.asc())
                .limit(limit)
            ).all()
            return [training_example_from_record(record) for record in records]

    def _create_or_get(
        self,
        *,
        user_id: int,
        turn: ConversationTurn,
        feedback_kind: TrainingFeedbackKind,
        status: TrainingFeedbackStatus,
        assistant_text: str | None,
        approved_at: datetime | None,
    ) -> TrainingExample:
        clean_user_text, _ = redact_training_text(turn.user_text)
        clean_assistant_text, _ = redact_training_text(assistant_text or "")
        if not clean_user_text:
            raise ValueError("Training example needs non-empty user text")
        with self.session_factory() as db:
            existing = db.scalar(
                select(TrainingExampleRecord).where(
                    TrainingExampleRecord.user_id == user_id,
                    TrainingExampleRecord.conversation_turn_id == turn.id,
                )
            )
            if existing is not None:
                return training_example_from_record(existing)
            record = TrainingExampleRecord(
                user_id=user_id,
                conversation_turn_id=turn.id,
                user_text=clean_user_text,
                assistant_text=clean_assistant_text or None,
                feedback_kind=feedback_kind.value,
                status=status.value,
                approved_at=approved_at,
            )
            db.add(record)
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
                existing = db.scalar(
                    select(TrainingExampleRecord).where(
                        TrainingExampleRecord.user_id == user_id,
                        TrainingExampleRecord.conversation_turn_id == turn.id,
                    )
                )
                if existing is None:
                    raise
                return training_example_from_record(existing)
            db.refresh(record)
            return training_example_from_record(record)


def training_example_from_record(record: TrainingExampleRecord) -> TrainingExample:
    return TrainingExample(
        id=record.id,
        user_id=record.user_id,
        conversation_turn_id=record.conversation_turn_id,
        user_text=record.user_text,
        assistant_text=record.assistant_text,
        feedback_kind=TrainingFeedbackKind(record.feedback_kind),
        status=TrainingFeedbackStatus(record.status),
        created_at=record.created_at,
        approved_at=record.approved_at,
    )
