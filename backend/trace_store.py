from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from backend.models import AgentActionRecord, AgentJobRecord, DeliveryOutboxRecord, Event


@dataclass(frozen=True)
class TraceJob:
    id: int
    user_id: int
    status: str
    goal: str
    created_at: datetime


@dataclass(frozen=True)
class TraceAction:
    id: int
    user_id: int
    job_id: int | None
    type: str
    status: str
    attempts: int
    depends_on_action_id: int | None
    compensation_status: str
    result_meta: dict[str, str]
    last_error: str | None
    created_at: datetime


@dataclass(frozen=True)
class TraceDelivery:
    id: int
    user_id: int
    status: str
    attempts: int
    last_error: str | None
    created_at: datetime


@dataclass(frozen=True)
class TraceEvent:
    id: int
    user_id: int
    type: str
    meta: dict | None
    created_at: datetime


@dataclass(frozen=True)
class TraceSnapshot:
    trace_id: str
    jobs: list[TraceJob]
    actions: list[TraceAction]
    deliveries: list[TraceDelivery]
    events: list[TraceEvent]

    @property
    def empty(self) -> bool:
        return not (self.jobs or self.actions or self.deliveries or self.events)


class SqlTraceStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def get(self, trace_id: str, *, user_id: int | None = None) -> TraceSnapshot:
        clean_trace_id = (trace_id or "").strip()
        with self.session_factory() as db:
            jobs_query = select(AgentJobRecord).where(AgentJobRecord.trace_id == clean_trace_id)
            actions_query = select(AgentActionRecord).where(AgentActionRecord.trace_id == clean_trace_id)
            deliveries_query = select(DeliveryOutboxRecord).where(DeliveryOutboxRecord.trace_id == clean_trace_id)
            events_query = select(Event).where(Event.trace_id == clean_trace_id)
            if user_id is not None:
                jobs_query = jobs_query.where(AgentJobRecord.user_id == user_id)
                actions_query = actions_query.where(AgentActionRecord.user_id == user_id)
                deliveries_query = deliveries_query.where(DeliveryOutboxRecord.user_id == user_id)
                events_query = events_query.where(Event.user_id == user_id)

            jobs = [
                TraceJob(
                    id=record.id,
                    user_id=record.user_id,
                    status=record.status,
                    goal=record.goal,
                    created_at=record.created_at,
                )
                for record in db.scalars(jobs_query.order_by(AgentJobRecord.created_at, AgentJobRecord.id)).all()
            ]
            actions = [
                TraceAction(
                    id=record.id,
                    user_id=record.user_id,
                    job_id=record.job_id,
                    type=record.type,
                    status=record.status,
                    attempts=record.attempts,
                    depends_on_action_id=record.depends_on_action_id,
                    compensation_status=record.compensation_status or "none",
                    result_meta=dict(record.result_meta or {}),
                    last_error=record.last_error,
                    created_at=record.created_at,
                )
                for record in db.scalars(actions_query.order_by(AgentActionRecord.created_at, AgentActionRecord.id)).all()
            ]
            deliveries = [
                TraceDelivery(
                    id=record.id,
                    user_id=record.user_id,
                    status=record.status,
                    attempts=record.attempts,
                    last_error=record.last_error,
                    created_at=record.created_at,
                )
                for record in db.scalars(
                    deliveries_query.order_by(DeliveryOutboxRecord.created_at, DeliveryOutboxRecord.id)
                ).all()
            ]
            events = [
                TraceEvent(
                    id=record.id,
                    user_id=record.user_id,
                    type=record.type,
                    meta=dict(record.meta or {}),
                    created_at=record.created_at,
                )
                for record in db.scalars(events_query.order_by(Event.created_at, Event.id)).all()
            ]
        return TraceSnapshot(
            trace_id=clean_trace_id,
            jobs=jobs,
            actions=actions,
            deliveries=deliveries,
            events=events,
        )
