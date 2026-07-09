from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from backend.models import InboundUpdateRecord


@dataclass(frozen=True)
class InboundUpdateClaim:
    acquired: bool
    status: str
    response: dict
    trace_id: str = ""


class SqlInboundUpdateStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def claim(self, user_id: int, idempotency_key: str, *, trace_id: str = "") -> InboundUpdateClaim:
        with self.session_factory() as db:
            record = InboundUpdateRecord(
                user_id=user_id,
                idempotency_key=idempotency_key,
                status="processing",
                trace_id=trace_id or None,
            )
            db.add(record)
            try:
                db.commit()
                return InboundUpdateClaim(True, "processing", {}, trace_id)
            except IntegrityError:
                db.rollback()
                existing = db.scalar(
                    select(InboundUpdateRecord).where(
                        InboundUpdateRecord.user_id == user_id,
                        InboundUpdateRecord.idempotency_key == idempotency_key,
                    )
                )
                if existing is None:
                    raise
                return InboundUpdateClaim(
                    False,
                    existing.status,
                    dict(existing.response or {}),
                    existing.trace_id or "",
                )

    def complete(
        self,
        user_id: int,
        idempotency_key: str,
        response: dict,
        *,
        trace_id: str = "",
    ) -> None:
        with self.session_factory() as db:
            record = db.scalar(
                select(InboundUpdateRecord).where(
                    InboundUpdateRecord.user_id == user_id,
                    InboundUpdateRecord.idempotency_key == idempotency_key,
                )
            )
            if record is None:
                raise KeyError(f"inbound update not found: {idempotency_key}")
            record.status = "completed"
            record.response = dict(response)
            record.trace_id = trace_id or record.trace_id
            db.commit()

    def mark_failed(self, user_id: int, idempotency_key: str, *, trace_id: str = "") -> None:
        with self.session_factory() as db:
            record = db.scalar(
                select(InboundUpdateRecord).where(
                    InboundUpdateRecord.user_id == user_id,
                    InboundUpdateRecord.idempotency_key == idempotency_key,
                )
            )
            if record is None:
                return
            record.status = "failed"
            record.trace_id = trace_id or record.trace_id
            db.commit()
