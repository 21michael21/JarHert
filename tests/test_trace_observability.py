from __future__ import annotations

from assistant.observability import delivery_latency_ms, queue_lag_ms, sanitize_observability_meta


def test_observability_metrics_measure_lag_without_user_text() -> None:
    from datetime import datetime, timedelta, timezone

    created = datetime(2026, 7, 10, 9, 0, tzinfo=timezone.utc)
    claimed = created + timedelta(seconds=3)
    sent = claimed + timedelta(seconds=2)

    assert queue_lag_ms(created, claimed) == 3_000
    assert delivery_latency_ms(created, sent) == 5_000
    assert queue_lag_ms(created.replace(tzinfo=None), claimed) == 3_000
    assert sanitize_observability_meta(
        {
            "provider": "openrouter",
            "prompt": "личный текст",
            "token": "secret",
            "latency_ms": 120,
            "nested": {"message": "private", "value": 1},
        }
    ) == {"provider": "openrouter", "latency_ms": 120, "nested": {"value": 1}}


def test_trace_viewer_never_prints_action_or_delivery_errors() -> None:
    from datetime import datetime

    from backend.trace_store import TraceAction, TraceDelivery, TraceSnapshot
    from gateway_bot.service import _format_trace

    snapshot = TraceSnapshot(
        trace_id="trace-private",
        jobs=[],
        actions=[
            TraceAction(
                id=1,
                user_id=1,
                job_id=1,
                type="task.create",
                status="failed",
                attempts=1,
                depends_on_action_id=None,
                compensation_status="none",
                result_meta={},
                last_error="личная задача и provider secret",
                created_at=datetime.now(),
            )
        ],
        deliveries=[
            TraceDelivery(
                id=1,
                user_id=1,
                status="failed",
                attempts=1,
                last_error="сообщение пользователя",
                created_at=datetime.now(),
            )
        ],
        events=[],
    )

    rendered = _format_trace(snapshot)

    assert "личная задача" not in rendered
    assert "сообщение пользователя" not in rendered
    assert "task.create" in rendered


def test_trace_viewer_shows_safe_latency_and_worker_metadata_only() -> None:
    from datetime import datetime

    from backend.trace_store import TraceEvent, TraceSnapshot
    from gateway_bot.service import _format_trace

    snapshot = TraceSnapshot(
        trace_id="trace-meta",
        jobs=[],
        actions=[],
        deliveries=[],
        events=[
            TraceEvent(
                id=1,
                user_id=1,
                type="provider_attempt_succeeded",
                meta={"provider": "openrouter_free", "latency_ms": 321, "prompt": "private prompt"},
                created_at=datetime.now(),
            ),
            TraceEvent(
                id=2,
                user_id=1,
                type="delivery_sent",
                meta={"delivery_id": 5, "delivery_latency_ms": 700, "message": "private"},
                created_at=datetime.now(),
            ),
            TraceEvent(
                id=3,
                user_id=1,
                type="worker_heartbeat",
                meta={"worker": "actions", "owner_id": "worker-1", "error": "private"},
                created_at=datetime.now(),
            ),
        ],
    )

    rendered = _format_trace(snapshot)

    assert "latency_ms=321" in rendered
    assert "delivery_latency_ms=700" in rendered
    assert "worker=actions" in rendered
    assert "private" not in rendered
