from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from assistant.automation_runtime import LeaseClaim, WorkerLease
from backend.models import AutomationWorkerLeaseRecord
from backend.store_converters import truncate_error


class SqlAutomationLeaseStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def try_acquire(
        self,
        worker_name: str,
        owner_id: str,
        *,
        now: datetime,
        lease_seconds: float,
    ) -> LeaseClaim | None:
        self._ensure_record(worker_name)
        with self.session_factory() as db:
            current = db.get(AutomationWorkerLeaseRecord, worker_name)
            if current is None:
                return None
            generation = current.generation + 1
            recovered = (
                (current.generation == 0 and current.last_completed_at is None)
                or current.status in {"retry_wait", "degraded"}
                or current.owner_id not in {None, owner_id}
            )
            result = db.execute(
                update(AutomationWorkerLeaseRecord)
                .where(
                    AutomationWorkerLeaseRecord.worker_name == worker_name,
                    AutomationWorkerLeaseRecord.generation == current.generation,
                    or_(
                        AutomationWorkerLeaseRecord.owner_id.is_(None),
                        AutomationWorkerLeaseRecord.lease_until.is_(None),
                        AutomationWorkerLeaseRecord.lease_until <= now,
                    ),
                    or_(
                        AutomationWorkerLeaseRecord.next_run_at.is_(None),
                        AutomationWorkerLeaseRecord.next_run_at <= now,
                    ),
                )
                .values(
                    status="running",
                    owner_id=owner_id,
                    generation=generation,
                    lease_until=now + timedelta(seconds=lease_seconds),
                    heartbeat_at=now,
                    last_started_at=now,
                    updated_at=now,
                )
                .execution_options(synchronize_session=False)
            )
            if result.rowcount != 1:
                db.rollback()
                return None
            db.commit()
            return LeaseClaim(worker_name, owner_id, generation, recovered)

    def heartbeat(self, claim: LeaseClaim, *, now: datetime, lease_seconds: float) -> bool:
        with self.session_factory() as db:
            result = db.execute(
                update(AutomationWorkerLeaseRecord)
                .where(*_owned_where(claim))
                .values(
                    heartbeat_at=now,
                    lease_until=now + timedelta(seconds=lease_seconds),
                    updated_at=now,
                )
                .execution_options(synchronize_session=False)
            )
            db.commit()
            return result.rowcount == 1

    def complete(self, claim: LeaseClaim, *, now: datetime, next_run_at: datetime) -> WorkerLease:
        return self._finish(
            claim,
            status="idle",
            now=now,
            next_run_at=next_run_at,
            failure_count=0,
            last_error=None,
        )

    def fail(
        self,
        claim: LeaseClaim,
        *,
        now: datetime,
        next_run_at: datetime,
        error: str,
        degraded: bool,
    ) -> WorkerLease:
        current = self.get(claim.worker_name)
        if current is None:
            raise RuntimeError(f"automation lease missing: {claim.worker_name}")
        return self._finish(
            claim,
            status="degraded" if degraded else "retry_wait",
            now=now,
            next_run_at=next_run_at,
            failure_count=current.failure_count + 1,
            last_error=truncate_error(error),
        )

    def get(self, worker_name: str) -> WorkerLease | None:
        with self.session_factory() as db:
            record = db.get(AutomationWorkerLeaseRecord, worker_name)
            return _from_record(record) if record is not None else None

    def list_all(self) -> list[WorkerLease]:
        with self.session_factory() as db:
            return [
                _from_record(record)
                for record in db.scalars(
                    select(AutomationWorkerLeaseRecord).order_by(AutomationWorkerLeaseRecord.worker_name.asc())
                ).all()
            ]

    def _ensure_record(self, worker_name: str) -> None:
        with self.session_factory() as db:
            if db.get(AutomationWorkerLeaseRecord, worker_name) is not None:
                return
            db.add(AutomationWorkerLeaseRecord(worker_name=worker_name))
            try:
                db.commit()
            except IntegrityError:
                db.rollback()

    def _finish(
        self,
        claim: LeaseClaim,
        *,
        status: str,
        now: datetime,
        next_run_at: datetime,
        failure_count: int,
        last_error: str | None,
    ) -> WorkerLease:
        with self.session_factory() as db:
            result = db.execute(
                update(AutomationWorkerLeaseRecord)
                .where(*_owned_where(claim))
                .values(
                    status=status,
                    owner_id=None,
                    lease_until=None,
                    heartbeat_at=now,
                    next_run_at=next_run_at,
                    last_completed_at=now,
                    failure_count=failure_count,
                    last_error=last_error,
                    updated_at=now,
                )
                .execution_options(synchronize_session=False)
            )
            if result.rowcount != 1:
                db.rollback()
                raise RuntimeError(f"automation lease lost: {claim.worker_name}")
            db.commit()
            record = db.get(AutomationWorkerLeaseRecord, claim.worker_name)
            return _from_record(record)


def _owned_where(claim: LeaseClaim) -> tuple:
    return (
        AutomationWorkerLeaseRecord.worker_name == claim.worker_name,
        AutomationWorkerLeaseRecord.owner_id == claim.owner_id,
        AutomationWorkerLeaseRecord.generation == claim.generation,
        AutomationWorkerLeaseRecord.status == "running",
    )


def _from_record(record: AutomationWorkerLeaseRecord) -> WorkerLease:
    return WorkerLease(
        worker_name=record.worker_name,
        status=record.status,
        owner_id=record.owner_id,
        generation=record.generation,
        failure_count=record.failure_count,
        lease_until=_aware(record.lease_until),
        heartbeat_at=_aware(record.heartbeat_at),
        next_run_at=_aware(record.next_run_at),
        last_started_at=_aware(record.last_started_at),
        last_completed_at=_aware(record.last_completed_at),
        last_error=record.last_error,
    )


def _aware(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)
