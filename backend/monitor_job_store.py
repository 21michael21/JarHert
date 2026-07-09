from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from assistant.monitors.models import MonitorJob, MonitorRun
from backend.models import MonitorJobRecord, MonitorRunRecord
from backend.store_converters import truncate_error


class SqlMonitorJobStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def create(
        self,
        *,
        user_id: int,
        chat_id: int,
        source_type: str,
        source_config: dict[str, str],
        condition_text: str,
        enabled: bool = True,
    ) -> MonitorJob:
        with self.session_factory() as db:
            record = MonitorJobRecord(
                user_id=user_id,
                chat_id=chat_id,
                source_type=source_type,
                source_config=dict(source_config),
                condition_text=condition_text.strip(),
                enabled=enabled,
            )
            db.add(record)
            db.commit()
            db.refresh(record)
            return _job_from_record(record)

    def list_enabled(self, *, limit: int = 50) -> list[MonitorJob]:
        with self.session_factory() as db:
            records = db.scalars(
                select(MonitorJobRecord)
                .where(MonitorJobRecord.enabled.is_(True))
                .order_by(MonitorJobRecord.created_at.asc(), MonitorJobRecord.id.asc())
                .limit(limit)
            ).all()
            return [_job_from_record(record) for record in records]

    def list_for_user(self, user_id: int, *, limit: int = 50) -> list[MonitorJob]:
        with self.session_factory() as db:
            records = db.scalars(
                select(MonitorJobRecord)
                .where(MonitorJobRecord.user_id == user_id)
                .order_by(MonitorJobRecord.enabled.desc(), MonitorJobRecord.created_at.desc(), MonitorJobRecord.id.desc())
                .limit(limit)
            ).all()
            return [_job_from_record(record) for record in records]

    def disable_for_user(self, user_id: int, monitor_job_id: int) -> bool:
        with self.session_factory() as db:
            record = db.get(MonitorJobRecord, monitor_job_id)
            if record is None or record.user_id != user_id:
                return False
            record.enabled = False
            db.commit()
            return True

    def mark_checked(
        self,
        monitor_job_id: int,
        *,
        state_hash: str,
        payload: dict[str, Any],
        checked_at: datetime | None = None,
    ) -> MonitorJob:
        with self.session_factory() as db:
            record = _require_job(db, monitor_job_id)
            record.last_state_hash = state_hash
            record.last_payload = dict(payload)
            record.last_checked_at = checked_at or datetime.now(timezone.utc)
            db.commit()
            db.refresh(record)
            return _job_from_record(record)

    def record_run(
        self,
        monitor_job_id: int,
        *,
        status: str,
        triggered: bool = False,
        error: str | None = None,
    ) -> MonitorRun:
        with self.session_factory() as db:
            _require_job(db, monitor_job_id)
            record = MonitorRunRecord(
                monitor_job_id=monitor_job_id,
                status=status,
                triggered=triggered,
                error=truncate_error(error or "") if error else None,
            )
            db.add(record)
            db.commit()
            db.refresh(record)
            return _run_from_record(record)

    def list_runs(self, monitor_job_id: int, *, limit: int = 20) -> list[MonitorRun]:
        with self.session_factory() as db:
            records = db.scalars(
                select(MonitorRunRecord)
                .where(MonitorRunRecord.monitor_job_id == monitor_job_id)
                .order_by(MonitorRunRecord.created_at.desc(), MonitorRunRecord.id.desc())
                .limit(limit)
            ).all()
            return [_run_from_record(record) for record in records]

    def get(self, monitor_job_id: int) -> MonitorJob | None:
        with self.session_factory() as db:
            record = db.get(MonitorJobRecord, monitor_job_id)
            return _job_from_record(record) if record is not None else None


def _require_job(db: Session, monitor_job_id: int) -> MonitorJobRecord:
    record = db.get(MonitorJobRecord, monitor_job_id)
    if record is None:
        raise KeyError(f"monitor job not found: {monitor_job_id}")
    return record


def _job_from_record(record: MonitorJobRecord) -> MonitorJob:
    return MonitorJob(
        id=record.id,
        user_id=record.user_id,
        chat_id=record.chat_id,
        source_type=record.source_type,
        source_config=dict(record.source_config or {}),
        condition_text=record.condition_text,
        enabled=record.enabled,
        last_state_hash=record.last_state_hash,
        last_payload=dict(record.last_payload or {}) if record.last_payload is not None else None,
        last_checked_at=record.last_checked_at,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _run_from_record(record: MonitorRunRecord) -> MonitorRun:
    return MonitorRun(
        id=record.id,
        monitor_job_id=record.monitor_job_id,
        status=record.status,
        triggered=record.triggered,
        error=record.error,
        created_at=record.created_at,
    )
