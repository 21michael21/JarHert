from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from assistant.automation_runtime import LeaseLostError
from assistant.coding_jobs import CodingJob
from backend.models import CodingJobRecord


class SqlCodingJobStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def enqueue(
        self,
        *,
        user_id: int,
        mode: str,
        prompt: str,
        repository_url: str | None = None,
        source_urls: list[str] | None = None,
        idempotency_key: str | None = None,
    ) -> CodingJob:
        with self.session_factory() as db:
            if idempotency_key:
                existing = db.scalar(select(CodingJobRecord).where(
                    CodingJobRecord.user_id == user_id,
                    CodingJobRecord.idempotency_key == idempotency_key,
                ))
                if existing is not None:
                    return _from_record(existing)
            record = CodingJobRecord(
                user_id=user_id,
                mode=mode,
                prompt=prompt.strip(),
                repository_url=repository_url,
                source_urls=list(source_urls or []),
                idempotency_key=idempotency_key,
            )
            db.add(record)
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
                if not idempotency_key:
                    raise
                record = db.scalar(select(CodingJobRecord).where(
                    CodingJobRecord.user_id == user_id,
                    CodingJobRecord.idempotency_key == idempotency_key,
                ))
                if record is None:
                    raise
            db.refresh(record)
            return _from_record(record)

    def claim_next(
        self,
        *,
        worker_id: str,
        now: datetime | None = None,
        lease_seconds: float = 900,
    ) -> CodingJob | None:
        claimed_at = now or datetime.now(timezone.utc)
        with self.session_factory() as db:
            db.execute(
                update(CodingJobRecord)
                .where(
                    CodingJobRecord.status == "running",
                    or_(CodingJobRecord.lease_until.is_(None), CodingJobRecord.lease_until <= claimed_at),
                )
                .values(status="queued", worker_id=None, lease_until=None, heartbeat_at=None)
            )
            candidates = db.scalars(
                select(CodingJobRecord)
                .where(CodingJobRecord.status == "queued")
                .order_by(CodingJobRecord.created_at.asc(), CodingJobRecord.id.asc())
                .limit(20)
            ).all()
            for candidate in candidates:
                result = db.execute(
                    update(CodingJobRecord)
                    .where(CodingJobRecord.id == candidate.id, CodingJobRecord.status == "queued")
                    .values(
                        status="running",
                        worker_id=worker_id,
                        heartbeat_at=claimed_at,
                        lease_until=claimed_at + timedelta(seconds=lease_seconds),
                    )
                    .execution_options(synchronize_session=False)
                )
                if result.rowcount == 1:
                    db.commit()
                    db.expire_all()
                    return _from_record(db.get(CodingJobRecord, candidate.id))
            db.commit()
            return None

    def heartbeat(self, job_id: int, *, worker_id: str, lease_seconds: float = 900) -> bool:
        now = datetime.now(timezone.utc)
        with self.session_factory() as db:
            result = db.execute(
                update(CodingJobRecord)
                .where(
                    CodingJobRecord.id == job_id,
                    CodingJobRecord.status == "running",
                    CodingJobRecord.worker_id == worker_id,
                )
                .values(heartbeat_at=now, lease_until=now + timedelta(seconds=lease_seconds))
            )
            db.commit()
            return result.rowcount == 1

    def list_for_user(self, user_id: int, *, limit: int = 20) -> list[CodingJob]:
        with self.session_factory() as db:
            records = db.scalars(
                select(CodingJobRecord)
                .where(CodingJobRecord.user_id == user_id)
                .order_by(CodingJobRecord.created_at.desc(), CodingJobRecord.id.desc())
                .limit(limit)
            ).all()
            return [_from_record(record) for record in records]

    def complete(self, job_id: int, *, worker_id: str, result_text: str) -> CodingJob:
        return self._finish(job_id, worker_id=worker_id, status="succeeded", result_text=result_text)

    def fail(self, job_id: int, *, worker_id: str, error: str) -> CodingJob:
        return self._finish(job_id, worker_id=worker_id, status="failed", error=error)

    def _finish(
        self,
        job_id: int,
        *,
        worker_id: str,
        status: str,
        result_text: str | None = None,
        error: str | None = None,
    ) -> CodingJob:
        with self.session_factory() as db:
            result = db.execute(
                update(CodingJobRecord)
                .where(
                    CodingJobRecord.id == job_id,
                    CodingJobRecord.status == "running",
                    CodingJobRecord.worker_id == worker_id,
                )
                .values(
                    status=status,
                    result_text=(result_text or "")[:20_000] or None,
                    last_error=(error or "")[:500] or None,
                    lease_until=None,
                )
            )
            if result.rowcount != 1:
                db.rollback()
                raise LeaseLostError(f"coding job lease lost: job_id={job_id} worker_id={worker_id}")
            db.commit()
            return _from_record(db.get(CodingJobRecord, job_id))


def _from_record(record: CodingJobRecord) -> CodingJob:
    return CodingJob(
        id=record.id,
        user_id=record.user_id,
        mode=record.mode,
        prompt=record.prompt,
        repository_url=record.repository_url,
        source_urls=list(record.source_urls or []),
        status=record.status,
        idempotency_key=record.idempotency_key,
        worker_id=record.worker_id,
        lease_until=_aware(record.lease_until),
        heartbeat_at=_aware(record.heartbeat_at),
        result_text=record.result_text,
        last_error=record.last_error,
        created_at=_aware(record.created_at),
        updated_at=_aware(record.updated_at),
    )


def _aware(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)
