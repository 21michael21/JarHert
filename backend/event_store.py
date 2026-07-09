from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from backend.models import Event


class EventStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def log(
        self,
        user_id: int,
        event_type: str,
        meta: dict | None = None,
        *,
        trace_id: str = "",
    ) -> None:
        with self.session_factory() as db:
            db.add(Event(user_id=user_id, type=event_type, trace_id=trace_id or None, meta=meta))
            db.commit()

    def log_assistant_response(
        self,
        user_id: int,
        event_type: str,
        *,
        intent: str,
        blocked_reason: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        fallback_count: int = 0,
        perf_ms: dict[str, int] | None = None,
        trace_id: str = "",
    ) -> None:
        self.log(
            user_id,
            event_type,
            {
                "intent": intent,
                "blocked_reason": blocked_reason,
                "provider": provider,
                "model": model,
                "fallback_count": fallback_count,
                "perf_ms": dict(perf_ms or {}),
            },
            trace_id=trace_id,
        )

    def recent_perf_samples(self, *, limit: int = 200) -> list[dict[str, int]]:
        with self.session_factory() as db:
            records = db.scalars(
                select(Event)
                .where(Event.type.like("assistant_%"))
                .order_by(Event.created_at.desc(), Event.id.desc())
                .limit(limit)
            ).all()
            samples: list[dict[str, int]] = []
            for record in records:
                perf_ms = (record.meta or {}).get("perf_ms") if record.meta else None
                if not isinstance(perf_ms, dict) or not perf_ms:
                    continue
                clean: dict[str, int] = {}
                for key, value in perf_ms.items():
                    if isinstance(key, str) and isinstance(value, int):
                        clean[key] = value
                if clean:
                    samples.append(clean)
            return samples
