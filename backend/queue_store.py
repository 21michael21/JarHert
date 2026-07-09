from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from assistant.action_queue import AgentAction, ActionStatus
from assistant.action_schema import ActionType
from assistant.agent_jobs import AgentJob
from assistant.delivery_outbox import DeliveryMessage, DeliveryStatus
from backend.models import AgentActionRecord, AgentJobRecord, DeliveryOutboxRecord
from backend.store_converters import (
    agent_action_from_record,
    agent_job_from_record,
    delivery_message_from_record,
    truncate_error,
)


class SqlAgentJobStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def create(self, user_id: int, goal: str, steps: list[str], *, trace_id: str = "") -> AgentJob:
        with self.session_factory() as db:
            record = AgentJobRecord(
                user_id=user_id,
                goal=goal.strip(),
                status="queued",
                steps=list(steps),
                trace_id=trace_id or None,
            )
            db.add(record)
            db.commit()
            db.refresh(record)
            return agent_job_from_record(record)

    def list_for_user(self, user_id: int, *, limit: int = 10) -> list[AgentJob]:
        with self.session_factory() as db:
            records = db.scalars(
                select(AgentJobRecord)
                .where(AgentJobRecord.user_id == user_id)
                .order_by(AgentJobRecord.created_at.desc(), AgentJobRecord.id.desc())
                .limit(limit)
            ).all()
            return [agent_job_from_record(record) for record in records]

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
            return agent_job_from_record(record)

    def mark_status(self, job_id: int, status: str, *, error: str | None = None) -> AgentJob:
        with self.session_factory() as db:
            record = db.get(AgentJobRecord, job_id)
            if record is None:
                raise KeyError(f"job not found: {job_id}")
            record.status = status
            record.error = truncate_error(error or "") if error else None
            db.commit()
            db.refresh(record)
            return agent_job_from_record(record)


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
        trace_id: str = "",
        depends_on_action_id: int | None = None,
        compensation_for_action_id: int | None = None,
        compensation_status: str = "none",
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
                    return agent_action_from_record(existing)
            record = AgentActionRecord(
                user_id=user_id,
                job_id=job_id,
                type=action_type.value,
                payload=dict(payload),
                status=status.value,
                trace_id=trace_id or None,
                depends_on_action_id=depends_on_action_id,
                compensation_for_action_id=compensation_for_action_id,
                compensation_status=compensation_status,
                idempotency_key=idempotency_key,
            )
            db.add(record)
            db.commit()
            db.refresh(record)
            return agent_action_from_record(record)

    def list_for_user(self, user_id: int, *, limit: int = 20) -> list[AgentAction]:
        with self.session_factory() as db:
            records = db.scalars(
                select(AgentActionRecord)
                .where(AgentActionRecord.user_id == user_id)
                .order_by(AgentActionRecord.created_at.desc(), AgentActionRecord.id.desc())
                .limit(limit)
            ).all()
            return [agent_action_from_record(record) for record in records]

    def claim_next(self) -> AgentAction | None:
        with self.session_factory() as db:
            records = db.scalars(
                select(AgentActionRecord)
                .where(AgentActionRecord.status == ActionStatus.QUEUED.value)
                .order_by(AgentActionRecord.created_at.asc(), AgentActionRecord.id.asc())
                .limit(50)
            ).all()
            for record in records:
                dependency_error = _dependency_error(db, record)
                if dependency_error == "":
                    continue
                if dependency_error:
                    record.status = ActionStatus.BLOCKED.value
                    record.last_error = truncate_error(dependency_error)
                    continue
                record.status = ActionStatus.RUNNING.value
                record.attempts += 1
                db.commit()
                db.refresh(record)
                return agent_action_from_record(record)
            db.commit()
            return None

    def mark_succeeded(self, action_id: int, *, result_meta: dict[str, str] | None = None) -> AgentAction:
        with self.session_factory() as db:
            record = _require_agent_action(db, action_id)
            record.status = ActionStatus.SUCCEEDED.value
            if result_meta is not None:
                record.result_meta = dict(result_meta)
            record.last_error = None
            _unblock_dependents_after_success(db, action_id)
            db.commit()
            db.refresh(record)
            return agent_action_from_record(record)

    def mark_failed(self, action_id: int, error: str) -> AgentAction:
        with self.session_factory() as db:
            record = _require_agent_action(db, action_id)
            record.status = ActionStatus.FAILED.value
            record.last_error = truncate_error(error)
            db.commit()
            db.refresh(record)
            return agent_action_from_record(record)

    def retry_failed(self, action_id: int) -> AgentAction:
        with self.session_factory() as db:
            record = _require_agent_action(db, action_id)
            record.status = ActionStatus.QUEUED.value
            record.last_error = None
            db.commit()
            db.refresh(record)
            return agent_action_from_record(record)

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

    def confirm_for_user(self, user_id: int, action_id: int) -> AgentAction | None:
        with self.session_factory() as db:
            record = db.scalar(
                select(AgentActionRecord).where(
                    AgentActionRecord.id == action_id,
                    AgentActionRecord.user_id == user_id,
                    AgentActionRecord.status == ActionStatus.NEEDS_CONFIRMATION.value,
                )
            )
            if record is None:
                return None
            record.status = ActionStatus.QUEUED.value
            db.commit()
            db.refresh(record)
            return agent_action_from_record(record)

    def confirm_job_for_user(self, user_id: int, job_id: int) -> list[AgentAction]:
        with self.session_factory() as db:
            records = db.scalars(
                select(AgentActionRecord)
                .where(
                    AgentActionRecord.user_id == user_id,
                    AgentActionRecord.job_id == job_id,
                    AgentActionRecord.status == ActionStatus.NEEDS_CONFIRMATION.value,
                )
                .order_by(AgentActionRecord.created_at.asc(), AgentActionRecord.id.asc())
            ).all()
            for record in records:
                record.status = ActionStatus.QUEUED.value
            db.commit()
            for record in records:
                db.refresh(record)
            return [agent_action_from_record(record) for record in records]

    def cancel_job_for_user(self, user_id: int, job_id: int) -> list[AgentAction]:
        with self.session_factory() as db:
            records = db.scalars(
                select(AgentActionRecord)
                .where(
                    AgentActionRecord.user_id == user_id,
                    AgentActionRecord.job_id == job_id,
                    AgentActionRecord.status.in_([ActionStatus.QUEUED.value, ActionStatus.NEEDS_CONFIRMATION.value]),
                )
                .order_by(AgentActionRecord.created_at.asc(), AgentActionRecord.id.asc())
            ).all()
            for record in records:
                record.status = ActionStatus.CANCELLED.value
            db.commit()
            for record in records:
                db.refresh(record)
            return [agent_action_from_record(record) for record in records]

    def block_dependents(self, action_id: int, reason: str) -> list[AgentAction]:
        with self.session_factory() as db:
            blocked = _block_dependents(db, action_id, reason)
            db.commit()
            return [agent_action_from_record(record) for record in blocked]

    def mark_compensation_skipped_for_job(self, job_id: int, failed_action_id: int, reason: str) -> list[AgentAction]:
        with self.session_factory() as db:
            records = db.scalars(
                select(AgentActionRecord)
                .where(
                    AgentActionRecord.job_id == job_id,
                    AgentActionRecord.id != failed_action_id,
                    AgentActionRecord.status == ActionStatus.SUCCEEDED.value,
                    AgentActionRecord.compensation_status == "none",
                )
                .order_by(AgentActionRecord.created_at.asc(), AgentActionRecord.id.asc())
            ).all()
            for record in records:
                result_meta = dict(record.result_meta or {})
                if _has_external_result_ids(result_meta):
                    record.compensation_status = "available"
                    record.last_error = truncate_error(
                        "Rollback identifiers are available, but no safe rollback tool is configured."
                    )
                else:
                    record.compensation_status = "not_supported"
                    record.last_error = truncate_error(reason)
            db.commit()
            for record in records:
                db.refresh(record)
            return [agent_action_from_record(record) for record in records]


class SqlDeliveryOutboxStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def enqueue(
        self,
        *,
        user_id: int,
        chat_id: int,
        text: str,
        trace_id: str = "",
        buttons: list[list[dict[str, str]]] | None = None,
        next_attempt_at: datetime | None = None,
    ) -> DeliveryMessage:
        with self.session_factory() as db:
            record = DeliveryOutboxRecord(
                user_id=user_id,
                chat_id=chat_id,
                text=text.strip(),
                status=DeliveryStatus.QUEUED.value,
                trace_id=trace_id or None,
                buttons=list(buttons or []),
                next_attempt_at=next_attempt_at,
            )
            db.add(record)
            db.commit()
            db.refresh(record)
            return delivery_message_from_record(record)

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
            return [delivery_message_from_record(record, status=DeliveryStatus.SENDING) for record in records]

    def mark_sent(self, message_id: int) -> DeliveryMessage:
        with self.session_factory() as db:
            record = _require_delivery_message(db, message_id)
            record.status = DeliveryStatus.SENT.value
            record.last_error = None
            record.next_attempt_at = None
            db.commit()
            db.refresh(record)
            return delivery_message_from_record(record)

    def mark_retry(self, message_id: int, error: str, next_attempt_at: datetime) -> DeliveryMessage:
        with self.session_factory() as db:
            record = _require_delivery_message(db, message_id)
            record.status = DeliveryStatus.QUEUED.value
            record.last_error = truncate_error(error)
            record.next_attempt_at = next_attempt_at
            db.commit()
            db.refresh(record)
            return delivery_message_from_record(record)

    def mark_failed_permanent(self, message_id: int, error: str) -> DeliveryMessage:
        with self.session_factory() as db:
            record = _require_delivery_message(db, message_id)
            record.status = DeliveryStatus.FAILED.value
            record.last_error = truncate_error(error)
            record.next_attempt_at = None
            db.commit()
            db.refresh(record)
            return delivery_message_from_record(record)

    def list_recent(self, *, limit: int = 20) -> list[DeliveryMessage]:
        with self.session_factory() as db:
            records = db.scalars(
                select(DeliveryOutboxRecord)
                .order_by(DeliveryOutboxRecord.created_at.desc(), DeliveryOutboxRecord.id.desc())
                .limit(limit)
            ).all()
            return [delivery_message_from_record(record) for record in records]

    def stats(self) -> dict[str, int]:
        with self.session_factory() as db:
            rows = db.execute(
                select(DeliveryOutboxRecord.status, func.count(DeliveryOutboxRecord.id)).group_by(
                    DeliveryOutboxRecord.status
                )
            ).all()
        counts = {status: count for status, count in rows}
        return {status.value: counts.get(status.value, 0) for status in DeliveryStatus}


def _require_agent_action(db: Session, action_id: int) -> AgentActionRecord:
    record = db.get(AgentActionRecord, action_id)
    if record is None:
        raise KeyError(f"action not found: {action_id}")
    return record


def _dependency_error(db: Session, record: AgentActionRecord) -> str | None:
    if record.depends_on_action_id is None:
        return None
    dependency = db.get(AgentActionRecord, record.depends_on_action_id)
    if dependency is None:
        return f"Dependency action #{record.depends_on_action_id} is missing."
    if dependency.status == ActionStatus.SUCCEEDED.value:
        return None
    if dependency.status in {ActionStatus.FAILED.value, ActionStatus.BLOCKED.value, ActionStatus.CANCELLED.value}:
        return f"Dependency action #{dependency.id} is {dependency.status}."
    return ""


def _block_dependents(db: Session, action_id: int, reason: str) -> list[AgentActionRecord]:
    records = db.scalars(
        select(AgentActionRecord)
        .where(
            AgentActionRecord.depends_on_action_id == action_id,
            AgentActionRecord.status.in_([ActionStatus.QUEUED.value, ActionStatus.NEEDS_CONFIRMATION.value]),
        )
        .order_by(AgentActionRecord.created_at.asc(), AgentActionRecord.id.asc())
    ).all()
    blocked: list[AgentActionRecord] = []
    for record in records:
        record.status = ActionStatus.BLOCKED.value
        record.last_error = truncate_error(reason)
        blocked.append(record)
        blocked.extend(_block_dependents(db, record.id, reason))
    return blocked


def _unblock_dependents_after_success(db: Session, action_id: int) -> None:
    records = db.scalars(
        select(AgentActionRecord).where(
            AgentActionRecord.depends_on_action_id == action_id,
            AgentActionRecord.status == ActionStatus.BLOCKED.value,
        )
    ).all()
    for record in records:
        record.status = ActionStatus.QUEUED.value
        record.last_error = None


def _has_external_result_ids(meta: dict[str, str]) -> bool:
    return any(key.endswith("_id") or key.endswith("_url") for key in meta)


def _require_delivery_message(db: Session, message_id: int) -> DeliveryOutboxRecord:
    record = db.get(DeliveryOutboxRecord, message_id)
    if record is None:
        raise KeyError(f"delivery message not found: {message_id}")
    return record
