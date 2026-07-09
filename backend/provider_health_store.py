from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from assistant.provider_router import ProviderFailureKind, ProviderHealth
from backend.models import ProviderHealthRecord
from backend.store_converters import provider_counter_field, provider_health_from_record


class SqlProviderHealthStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def get(self, name: str) -> ProviderHealth:
        with self.session_factory() as db:
            record = db.scalar(select(ProviderHealthRecord).where(ProviderHealthRecord.name == name))
            if record is None:
                return ProviderHealth(name=name)
            return provider_health_from_record(record)

    def list_all(self) -> list[ProviderHealth]:
        with self.session_factory() as db:
            records = db.scalars(
                select(ProviderHealthRecord).order_by(ProviderHealthRecord.id.asc())
            ).all()
            return [provider_health_from_record(record) for record in records]

    def record_success(self, name: str, model: str, *, latency_ms: int | None = None) -> ProviderHealth:
        with self.session_factory() as db:
            record = _get_or_create_provider_health(db, name)
            record.model = model
            record.last_success_at = datetime.now(timezone.utc)
            record.latency_ms = latency_ms
            record.cooldown_until = None
            db.commit()
            db.refresh(record)
            return provider_health_from_record(record)

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
            counter = provider_counter_field(failure_kind)
            setattr(record, counter, getattr(record, counter) + 1)
            db.commit()
            db.refresh(record)
            return provider_health_from_record(record)


def _get_or_create_provider_health(db: Session, name: str) -> ProviderHealthRecord:
    record = db.scalar(select(ProviderHealthRecord).where(ProviderHealthRecord.name == name))
    if record is None:
        record = ProviderHealthRecord(name=name)
        db.add(record)
        db.flush()
    return record
