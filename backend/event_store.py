from __future__ import annotations

from sqlalchemy.orm import Session, sessionmaker

from backend.models import Event


class EventStore:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def log(self, user_id: int, event_type: str, meta: dict | None = None) -> None:
        with self.session_factory() as db:
            db.add(Event(user_id=user_id, type=event_type, meta=meta))
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
        )
