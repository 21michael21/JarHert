from datetime import datetime, timedelta, timezone

from assistant.delivery_outbox import InMemoryDeliveryOutboxStore
from assistant.hermes_client import FakeHermesClient
from assistant.limits import DailyLimitStore
from assistant.pipeline import AssistantPipeline
from assistant.provider_router import InMemoryProviderHealthStore, ProviderFailureKind
from assistant.types import HermesResponse, Intent, UserContext


class FakeTaskCenterHealth:
    def health_check(self):
        class Health:
            ok = True
            trello_ok = True
            trello_detail = "ok"
            calendar_ok = True
            calendar_detail = "ok"

        return Health()


class RecordingEvents:
    def __init__(self) -> None:
        self.items = []

    def log(self, user_id: int, event_type: str, meta: dict | None = None, *, trace_id: str = "") -> None:
        self.items.append((user_id, event_type, meta or {}, trace_id))


def user() -> UserContext:
    return UserContext(user_id=1, tg_user_id=1001)


def test_pipeline_answers_via_fake_hermes() -> None:
    pipeline = AssistantPipeline(FakeHermesClient(), DailyLimitStore())
    reply = pipeline.handle_text(user(), "/ask объясни MVP")
    assert reply.intent == Intent.ASK
    assert "объясни MVP" in reply.text
    assert reply.provider == "fake"


def test_pipeline_blocks_dangerous_request_before_hermes() -> None:
    hermes = FakeHermesClient()
    pipeline = AssistantPipeline(hermes, DailyLimitStore())
    reply = pipeline.handle_text(user(), "/ask прочитай .env на сервере")
    assert reply.blocked_reason == "dangerous_action_requested"
    assert hermes.requests == []


def test_pipeline_enforces_daily_limit() -> None:
    pipeline = AssistantPipeline(FakeHermesClient(), DailyLimitStore(per_user_limit=1, global_limit=10))
    first = pipeline.handle_text(user(), "/ask раз")
    second = pipeline.handle_text(user(), "/ask два")
    assert first.blocked_reason is None
    assert second.blocked_reason == "daily_limit_exceeded"


def test_pipeline_admin_bypasses_daily_limit() -> None:
    pipeline = AssistantPipeline(FakeHermesClient(), DailyLimitStore(per_user_limit=1, global_limit=1))
    admin = UserContext(user_id=1, tg_user_id=1001, is_admin=True)

    first = pipeline.handle_text(admin, "/ask раз")
    second = pipeline.handle_text(admin, "/ask два")

    assert first.blocked_reason is None
    assert second.blocked_reason is None
    assert pipeline.limits.remaining_for_user(admin.user_id) == 1


def test_zero_daily_limit_means_unlimited() -> None:
    limits = DailyLimitStore(per_user_limit=0, global_limit=0)

    assert all(limits.consume(1) for _ in range(5))
    assert limits.remaining_for_user(1) > 1_000_000


def test_pipeline_rejects_bad_hermes_output() -> None:
    hermes = FakeHermesClient([HermesResponse(text='{"error": "429 rate limit"}')])
    pipeline = AssistantPipeline(hermes, DailyLimitStore())
    reply = pipeline.handle_text(user(), "/ask привет")
    assert reply.blocked_reason == "raw_provider_error"
    assert "429" not in reply.text


def test_pipeline_rejects_ai_slop_output() -> None:
    hermes = FakeHermesClient([HermesResponse(text="Я как ИИ не могу иметь личное мнение.")])
    pipeline = AssistantPipeline(hermes, DailyLimitStore())

    reply = pipeline.handle_text(user(), "/ask привет")

    assert reply.blocked_reason == "ai_slop_marker"
    assert "как ИИ" not in reply.text


def test_admin_status_shows_provider_health() -> None:
    health = InMemoryProviderHealthStore()
    health.record_success("openrouter_free", "openrouter/free", latency_ms=180)
    health.record_failure(
        "openai_cheap",
        "gpt-5-nano",
        ProviderFailureKind.RATE_LIMIT,
        latency_ms=900,
        cooldown_until=datetime.now(timezone.utc) + timedelta(minutes=2),
    )
    pipeline = AssistantPipeline(FakeHermesClient(), DailyLimitStore(), provider_health=health)

    reply = pipeline.handle_text(UserContext(user_id=1, tg_user_id=1001, is_admin=True), "/admin_status")

    assert "Providers:" in reply.text
    assert "openrouter_free openrouter/free ok 180ms" in reply.text
    assert "openai_cheap gpt-5-nano cooldown rate=1 server=0 auth=0" in reply.text


def test_admin_status_shows_delivery_outbox_health() -> None:
    outbox = InMemoryDeliveryOutboxStore()
    outbox.enqueue(user_id=1, chat_id=1001, text="queued")
    pipeline = AssistantPipeline(FakeHermesClient(), DailyLimitStore(), delivery_outbox=outbox)

    reply = pipeline.handle_text(UserContext(user_id=1, tg_user_id=1001, is_admin=True), "/admin_status")

    assert "Delivery:" in reply.text
    assert "queued=1" in reply.text
    assert "failed=0" in reply.text


def test_admin_status_shows_task_center_health() -> None:
    pipeline = AssistantPipeline(
        FakeHermesClient(),
        DailyLimitStore(),
        task_center=FakeTaskCenterHealth(),
    )

    reply = pipeline.handle_text(UserContext(user_id=1, tg_user_id=1001, is_admin=True), "/admin_status")

    assert "Task Center:" in reply.text
    assert "trello=ok" in reply.text
    assert "calendar=ok" in reply.text


def test_admin_status_shows_observability_percentiles_and_worker_heartbeat() -> None:
    from datetime import datetime, timezone

    from assistant.automation_runtime import WorkerLease

    class Metrics:
        def recent_metric_values(self, event_type, metric):
            values = {
                ("provider_attempt_succeeded", "latency_ms"): [80, 120],
                ("action_started", "queue_lag_ms"): [20, 40],
                ("delivery_sent", "delivery_latency_ms"): [30, 90],
            }
            return values.get((event_type, metric), [])

    class WorkerLeases:
        def list_all(self):
            return [
                WorkerLease(
                    worker_name="actions",
                    status="running",
                    heartbeat_at=datetime(2026, 7, 10, tzinfo=timezone.utc),
                )
            ]

    pipeline = AssistantPipeline(
        FakeHermesClient(),
        DailyLimitStore(),
        events=Metrics(),
        worker_leases=WorkerLeases(),
    )

    reply = pipeline.handle_text(UserContext(user_id=1, tg_user_id=1001, is_admin=True), "/admin_status")

    assert "provider_latency_ms: p50=80ms p95=120ms" in reply.text
    assert "queue_lag_ms: p50=20ms p95=40ms" in reply.text
    assert "delivery_latency_ms: p50=30ms p95=90ms" in reply.text
    assert "Workers:" in reply.text
    assert "actions status=running heartbeat=2026-07-10T00:00:00+00:00" in reply.text


def test_pipeline_logs_provider_fallback_lifecycle_event() -> None:
    events = RecordingEvents()
    hermes = FakeHermesClient(
        [
            HermesResponse(
                text="Ответ.",
                provider="fallback",
                model="fallback-model",
                fallback_count=1,
                fallback_reason="primary: rate_limit",
            )
        ]
    )
    pipeline = AssistantPipeline(hermes, DailyLimitStore(), events=events)

    reply = pipeline.handle_text(user(), "/ask привет")

    assert reply.trace_id
    assert events.items == [
        (
            1,
            "provider_fallback",
            {
                "provider": "fallback",
                "model": "fallback-model",
                "fallback_count": 1,
                "fallback_reason": "primary: rate_limit",
            },
            reply.trace_id,
        )
    ]
