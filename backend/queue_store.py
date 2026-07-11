from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from assistant.action_queue import AgentAction, ActionStatus
from assistant.action_schema import ActionType
from assistant.agent_jobs import AgentJob
from assistant.automation_runtime import LeaseLostError
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

    def create(
        self,
        user_id: int,
        goal: str,
        steps: list[str],
        *,
        trace_id: str = "",
        idempotency_key: str | None = None,
    ) -> AgentJob:
        with self.session_factory() as db:
            if idempotency_key:
                existing = db.scalar(
                    select(AgentJobRecord).where(
                        AgentJobRecord.user_id == user_id,
                        AgentJobRecord.idempotency_key == idempotency_key,
                    )
                )
                if existing is not None:
                    return agent_job_from_record(existing)
            record = AgentJobRecord(
                user_id=user_id,
                goal=goal.strip(),
                status="queued",
                steps=list(steps),
                trace_id=trace_id or None,
                idempotency_key=idempotency_key,
            )
            db.add(record)
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
                if not idempotency_key:
                    raise
                existing = db.scalar(
                    select(AgentJobRecord).where(
                        AgentJobRecord.user_id == user_id,
                        AgentJobRecord.idempotency_key == idempotency_key,
                    )
                )
                if existing is None:
                    raise
                return agent_job_from_record(existing)
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
        depends_on_action_ids: tuple[int, ...] | list[int] | None = None,
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
            dependency_ids = _normalize_dependency_ids(depends_on_action_id, depends_on_action_ids)
            record = AgentActionRecord(
                user_id=user_id,
                job_id=job_id,
                type=action_type.value,
                payload=dict(payload),
                status=status.value,
                trace_id=trace_id or None,
                depends_on_action_id=dependency_ids[0] if dependency_ids else None,
                depends_on_action_ids=list(dependency_ids),
                compensation_for_action_id=compensation_for_action_id,
                compensation_status=compensation_status,
                idempotency_key=idempotency_key,
            )
            db.add(record)
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
                if not idempotency_key:
                    raise
                existing = db.scalar(
                    select(AgentActionRecord).where(
                        AgentActionRecord.user_id == user_id,
                        AgentActionRecord.idempotency_key == idempotency_key,
                    )
                )
                if existing is None:
                    raise
                return agent_action_from_record(existing)
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

    def claim_next(
        self,
        *,
        worker_id: str | None = None,
        now: datetime | None = None,
        lease_seconds: float = 75,
    ) -> AgentAction | None:
        claimed_at = now or datetime.now(timezone.utc)
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
                result = db.execute(
                    update(AgentActionRecord)
                    .where(
                        AgentActionRecord.id == record.id,
                        AgentActionRecord.status == ActionStatus.QUEUED.value,
                    )
                    .values(
                        status=ActionStatus.RUNNING.value,
                        attempts=AgentActionRecord.attempts + 1,
                        worker_id=worker_id,
                        claimed_at=claimed_at,
                        heartbeat_at=claimed_at,
                        lease_until=claimed_at + timedelta(seconds=lease_seconds),
                    )
                    .execution_options(synchronize_session=False)
                )
                if result.rowcount != 1:
                    continue
                db.commit()
                db.expire_all()
                claimed = db.get(AgentActionRecord, record.id)
                return agent_action_from_record(claimed)
            db.commit()
            return None

    def recover_expired(self, *, now: datetime | None = None) -> int:
        expired_at = now or datetime.now(timezone.utc)
        with self.session_factory() as db:
            result = db.execute(
                update(AgentActionRecord)
                .where(
                    AgentActionRecord.status == ActionStatus.RUNNING.value,
                    or_(AgentActionRecord.lease_until.is_(None), AgentActionRecord.lease_until <= expired_at),
                )
                .values(
                    status=ActionStatus.QUEUED.value,
                    worker_id=None,
                    lease_until=None,
                    claimed_at=None,
                    heartbeat_at=None,
                )
                .execution_options(synchronize_session=False)
            )
            db.commit()
            return result.rowcount

    def recover_running(self) -> int:
        with self.session_factory() as db:
            result = db.execute(
                update(AgentActionRecord)
                .where(AgentActionRecord.status == ActionStatus.RUNNING.value)
                .values(
                    status=ActionStatus.QUEUED.value,
                    worker_id=None,
                    lease_until=None,
                    claimed_at=None,
                    heartbeat_at=None,
                )
                .execution_options(synchronize_session=False)
            )
            db.commit()
            return result.rowcount

    def heartbeat(
        self,
        action_id: int,
        *,
        worker_id: str,
        now: datetime | None = None,
        lease_seconds: float = 75,
    ) -> bool:
        heartbeat_at = now or datetime.now(timezone.utc)
        with self.session_factory() as db:
            result = db.execute(
                update(AgentActionRecord)
                .where(
                    AgentActionRecord.id == action_id,
                    AgentActionRecord.status == ActionStatus.RUNNING.value,
                    AgentActionRecord.worker_id == worker_id,
                )
                .values(
                    heartbeat_at=heartbeat_at,
                    lease_until=heartbeat_at + timedelta(seconds=lease_seconds),
                )
                .execution_options(synchronize_session=False)
            )
            db.commit()
            return result.rowcount == 1

    def mark_succeeded(
        self,
        action_id: int,
        *,
        result_meta: dict[str, str] | None = None,
        result_text: str | None = None,
        worker_id: str | None = None,
    ) -> AgentAction:
        with self.session_factory() as db:
            if worker_id is not None:
                values = {
                    "status": ActionStatus.SUCCEEDED.value,
                    "last_error": None,
                    "lease_until": None,
                }
                if result_meta is not None:
                    values["result_meta"] = dict(result_meta)
                if result_text is not None:
                    values["result_text"] = result_text
                _require_owned_update(db, action_id, worker_id, values)
                _unblock_dependents_after_success(db, action_id)
                db.commit()
                return agent_action_from_record(_require_agent_action(db, action_id))
            record = _require_agent_action(db, action_id)
            record.status = ActionStatus.SUCCEEDED.value
            if result_meta is not None:
                record.result_meta = dict(result_meta)
            if result_text is not None:
                record.result_text = result_text
            record.last_error = None
            _unblock_dependents_after_success(db, action_id)
            db.commit()
            db.refresh(record)
            return agent_action_from_record(record)

    def mark_failed(self, action_id: int, error: str, *, worker_id: str | None = None) -> AgentAction:
        with self.session_factory() as db:
            if worker_id is not None:
                _require_owned_update(
                    db,
                    action_id,
                    worker_id,
                    {
                        "status": ActionStatus.FAILED.value,
                        "last_error": truncate_error(error),
                        "lease_until": None,
                    },
                )
                db.commit()
                return agent_action_from_record(_require_agent_action(db, action_id))
            record = _require_agent_action(db, action_id)
            record.status = ActionStatus.FAILED.value
            record.last_error = truncate_error(error)
            db.commit()
            db.refresh(record)
            return agent_action_from_record(record)

    def retry_failed(self, action_id: int, *, worker_id: str | None = None) -> AgentAction:
        with self.session_factory() as db:
            if worker_id is not None:
                _require_owned_update(
                    db,
                    action_id,
                    worker_id,
                    {
                        "status": ActionStatus.QUEUED.value,
                        "last_error": None,
                        "worker_id": None,
                        "lease_until": None,
                        "claimed_at": None,
                        "heartbeat_at": None,
                    },
                )
                db.commit()
                return agent_action_from_record(_require_agent_action(db, action_id))
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
                    AgentActionRecord.status.in_([
                        ActionStatus.QUEUED.value,
                        ActionStatus.NEEDS_CONFIRMATION.value,
                        ActionStatus.PAUSED.value,
                    ]),
                )
                .order_by(AgentActionRecord.created_at.asc(), AgentActionRecord.id.asc())
            ).all()
            for record in records:
                record.status = ActionStatus.CANCELLED.value
            db.commit()
            for record in records:
                db.refresh(record)
            return [agent_action_from_record(record) for record in records]

    def pause_job_for_user(self, user_id: int, job_id: int) -> list[AgentAction]:
        return self._change_job_status(user_id, job_id, ActionStatus.QUEUED, ActionStatus.PAUSED)

    def resume_job_for_user(self, user_id: int, job_id: int) -> list[AgentAction]:
        return self._change_job_status(user_id, job_id, ActionStatus.PAUSED, ActionStatus.QUEUED)

    def _change_job_status(
        self,
        user_id: int,
        job_id: int,
        current: ActionStatus,
        target: ActionStatus,
    ) -> list[AgentAction]:
        with self.session_factory() as db:
            records = db.scalars(
                select(AgentActionRecord)
                .where(
                    AgentActionRecord.user_id == user_id,
                    AgentActionRecord.job_id == job_id,
                    AgentActionRecord.status == current.value,
                )
                .order_by(AgentActionRecord.created_at.asc(), AgentActionRecord.id.asc())
            ).all()
            for record in records:
                record.status = target.value
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
        idempotency_key: str | None = None,
    ) -> DeliveryMessage:
        with self.session_factory() as db:
            if idempotency_key:
                existing = db.scalar(
                    select(DeliveryOutboxRecord).where(
                        DeliveryOutboxRecord.user_id == user_id,
                        DeliveryOutboxRecord.idempotency_key == idempotency_key,
                    )
                )
                if existing is not None:
                    return delivery_message_from_record(existing)
            record = DeliveryOutboxRecord(
                user_id=user_id,
                chat_id=chat_id,
                text=text.strip(),
                status=DeliveryStatus.QUEUED.value,
                trace_id=trace_id or None,
                idempotency_key=idempotency_key,
                buttons=list(buttons or []),
                next_attempt_at=next_attempt_at,
            )
            db.add(record)
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
                if not idempotency_key:
                    raise
                existing = db.scalar(
                    select(DeliveryOutboxRecord).where(
                        DeliveryOutboxRecord.user_id == user_id,
                        DeliveryOutboxRecord.idempotency_key == idempotency_key,
                    )
                )
                if existing is None:
                    raise
                return delivery_message_from_record(existing)
            db.refresh(record)
            return delivery_message_from_record(record)

    def claim_due(
        self,
        *,
        now: datetime | None = None,
        limit: int = 20,
        worker_id: str | None = None,
        lease_seconds: float = 60,
    ) -> list[DeliveryMessage]:
        due_at = now or datetime.now(timezone.utc)
        with self.session_factory() as db:
            candidate_ids = db.scalars(
                select(DeliveryOutboxRecord)
                .where(
                    DeliveryOutboxRecord.status == DeliveryStatus.QUEUED.value,
                    (
                        DeliveryOutboxRecord.next_attempt_at.is_(None)
                        | (DeliveryOutboxRecord.next_attempt_at <= due_at)
                    ),
                )
                .order_by(DeliveryOutboxRecord.created_at.asc(), DeliveryOutboxRecord.id.asc())
                .limit(max(limit * 3, limit))
            ).all()
            claimed: list[DeliveryMessage] = []
            for candidate in candidate_ids:
                if len(claimed) >= limit:
                    break
                result = db.execute(
                    update(DeliveryOutboxRecord)
                    .where(
                        DeliveryOutboxRecord.id == candidate.id,
                        DeliveryOutboxRecord.status == DeliveryStatus.QUEUED.value,
                        or_(
                            DeliveryOutboxRecord.next_attempt_at.is_(None),
                            DeliveryOutboxRecord.next_attempt_at <= due_at,
                        ),
                    )
                    .values(
                        status=DeliveryStatus.SENDING.value,
                        attempts=DeliveryOutboxRecord.attempts + 1,
                        worker_id=worker_id,
                        claimed_at=due_at,
                        heartbeat_at=due_at,
                        lease_until=due_at + timedelta(seconds=lease_seconds),
                    )
                    .execution_options(synchronize_session=False)
                )
                if result.rowcount != 1:
                    continue
                db.commit()
                db.expire_all()
                record = db.get(DeliveryOutboxRecord, candidate.id)
                claimed.append(delivery_message_from_record(record))
            return claimed

    def recover_expired(self, *, now: datetime | None = None) -> int:
        expired_at = now or datetime.now(timezone.utc)
        with self.session_factory() as db:
            result = db.execute(
                update(DeliveryOutboxRecord)
                .where(
                    DeliveryOutboxRecord.status == DeliveryStatus.SENDING.value,
                    or_(DeliveryOutboxRecord.lease_until.is_(None), DeliveryOutboxRecord.lease_until <= expired_at),
                )
                .values(
                    status=DeliveryStatus.QUEUED.value,
                    worker_id=None,
                    lease_until=None,
                    claimed_at=None,
                    heartbeat_at=None,
                )
                .execution_options(synchronize_session=False)
            )
            db.commit()
            return result.rowcount

    def recover_sending(self) -> int:
        with self.session_factory() as db:
            result = db.execute(
                update(DeliveryOutboxRecord)
                .where(DeliveryOutboxRecord.status == DeliveryStatus.SENDING.value)
                .values(
                    status=DeliveryStatus.QUEUED.value,
                    worker_id=None,
                    lease_until=None,
                    claimed_at=None,
                    heartbeat_at=None,
                )
                .execution_options(synchronize_session=False)
            )
            db.commit()
            return result.rowcount

    def heartbeat(
        self,
        message_id: int,
        *,
        worker_id: str,
        now: datetime | None = None,
        lease_seconds: float = 60,
    ) -> bool:
        heartbeat_at = now or datetime.now(timezone.utc)
        with self.session_factory() as db:
            result = db.execute(
                update(DeliveryOutboxRecord)
                .where(
                    DeliveryOutboxRecord.id == message_id,
                    DeliveryOutboxRecord.status == DeliveryStatus.SENDING.value,
                    DeliveryOutboxRecord.worker_id == worker_id,
                )
                .values(
                    heartbeat_at=heartbeat_at,
                    lease_until=heartbeat_at + timedelta(seconds=lease_seconds),
                )
                .execution_options(synchronize_session=False)
            )
            db.commit()
            return result.rowcount == 1

    def mark_sent(self, message_id: int, *, worker_id: str | None = None) -> DeliveryMessage:
        with self.session_factory() as db:
            if worker_id is not None:
                _require_owned_delivery_update(
                    db,
                    message_id,
                    worker_id,
                    {
                        "status": DeliveryStatus.SENT.value,
                        "last_error": None,
                        "next_attempt_at": None,
                        "lease_until": None,
                    },
                )
                db.commit()
                return delivery_message_from_record(_require_delivery_message(db, message_id))
            record = _require_delivery_message(db, message_id)
            record.status = DeliveryStatus.SENT.value
            record.last_error = None
            record.next_attempt_at = None
            db.commit()
            db.refresh(record)
            return delivery_message_from_record(record)

    def mark_retry(
        self,
        message_id: int,
        error: str,
        next_attempt_at: datetime,
        *,
        worker_id: str | None = None,
    ) -> DeliveryMessage:
        with self.session_factory() as db:
            if worker_id is not None:
                _require_owned_delivery_update(
                    db,
                    message_id,
                    worker_id,
                    {
                        "status": DeliveryStatus.QUEUED.value,
                        "last_error": truncate_error(error),
                        "next_attempt_at": next_attempt_at,
                        "worker_id": None,
                        "lease_until": None,
                        "claimed_at": None,
                        "heartbeat_at": None,
                    },
                )
                db.commit()
                return delivery_message_from_record(_require_delivery_message(db, message_id))
            record = _require_delivery_message(db, message_id)
            record.status = DeliveryStatus.QUEUED.value
            record.last_error = truncate_error(error)
            record.next_attempt_at = next_attempt_at
            db.commit()
            db.refresh(record)
            return delivery_message_from_record(record)

    def mark_failed_permanent(
        self,
        message_id: int,
        error: str,
        *,
        worker_id: str | None = None,
    ) -> DeliveryMessage:
        with self.session_factory() as db:
            if worker_id is not None:
                _require_owned_delivery_update(
                    db,
                    message_id,
                    worker_id,
                    {
                        "status": DeliveryStatus.FAILED.value,
                        "last_error": truncate_error(error),
                        "next_attempt_at": None,
                        "lease_until": None,
                    },
                )
                db.commit()
                return delivery_message_from_record(_require_delivery_message(db, message_id))
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


def _require_owned_update(
    db: Session,
    action_id: int,
    worker_id: str,
    values: dict,
) -> None:
    result = db.execute(
        update(AgentActionRecord)
        .where(
            AgentActionRecord.id == action_id,
            AgentActionRecord.status == ActionStatus.RUNNING.value,
            AgentActionRecord.worker_id == worker_id,
        )
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        raise LeaseLostError(f"action lease lost: action_id={action_id} worker_id={worker_id}")


def _dependency_error(db: Session, record: AgentActionRecord) -> str | None:
    dependency_ids = _record_dependency_ids(record)
    if not dependency_ids:
        return None
    waiting = False
    for dependency_id in dependency_ids:
        dependency = db.get(AgentActionRecord, dependency_id)
        if dependency is None:
            return f"Dependency action #{dependency_id} is missing."
        if dependency.status in {ActionStatus.FAILED.value, ActionStatus.BLOCKED.value, ActionStatus.CANCELLED.value}:
            return f"Dependency action #{dependency.id} is {dependency.status}."
        if dependency.status != ActionStatus.SUCCEEDED.value:
            waiting = True
    return "" if waiting else None


def _block_dependents(db: Session, action_id: int, reason: str) -> list[AgentActionRecord]:
    records = db.scalars(
        select(AgentActionRecord)
        .where(
            AgentActionRecord.status.in_([ActionStatus.QUEUED.value, ActionStatus.NEEDS_CONFIRMATION.value]),
        )
        .order_by(AgentActionRecord.created_at.asc(), AgentActionRecord.id.asc())
    ).all()
    blocked: list[AgentActionRecord] = []
    for record in records:
        if action_id not in _record_dependency_ids(record):
            continue
        record.status = ActionStatus.BLOCKED.value
        record.last_error = truncate_error(reason)
        blocked.append(record)
        blocked.extend(_block_dependents(db, record.id, reason))
    return blocked


def _unblock_dependents_after_success(db: Session, action_id: int) -> None:
    records = db.scalars(
        select(AgentActionRecord).where(
            AgentActionRecord.status == ActionStatus.BLOCKED.value,
        )
    ).all()
    for record in records:
        if action_id not in _record_dependency_ids(record):
            continue
        if _dependency_error(db, record) is not None:
            continue
        record.status = ActionStatus.QUEUED.value
        record.last_error = None


def _normalize_dependency_ids(
    depends_on_action_id: int | None,
    depends_on_action_ids: tuple[int, ...] | list[int] | None,
) -> tuple[int, ...]:
    values = [int(value) for value in (depends_on_action_ids or ())]
    if depends_on_action_id is not None:
        values.insert(0, int(depends_on_action_id))
    return tuple(dict.fromkeys(values))


def _record_dependency_ids(record: AgentActionRecord) -> tuple[int, ...]:
    values = tuple(int(value) for value in (record.depends_on_action_ids or ()))
    if values:
        return values
    return (int(record.depends_on_action_id),) if record.depends_on_action_id is not None else ()


def _has_external_result_ids(meta: dict[str, str]) -> bool:
    return any(key.endswith("_id") or key.endswith("_url") for key in meta)


def _require_delivery_message(db: Session, message_id: int) -> DeliveryOutboxRecord:
    record = db.get(DeliveryOutboxRecord, message_id)
    if record is None:
        raise KeyError(f"delivery message not found: {message_id}")
    return record


def _require_owned_delivery_update(
    db: Session,
    message_id: int,
    worker_id: str,
    values: dict,
) -> None:
    result = db.execute(
        update(DeliveryOutboxRecord)
        .where(
            DeliveryOutboxRecord.id == message_id,
            DeliveryOutboxRecord.status == DeliveryStatus.SENDING.value,
            DeliveryOutboxRecord.worker_id == worker_id,
        )
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        raise LeaseLostError(f"delivery lease lost: message_id={message_id} worker_id={worker_id}")
