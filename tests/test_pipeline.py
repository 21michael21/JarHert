from datetime import datetime, timedelta, timezone

from assistant.delivery_outbox import InMemoryDeliveryOutboxStore
from assistant.hermes_client import FakeHermesClient
from assistant.limits import DailyLimitStore
from assistant.pipeline import AssistantPipeline
from assistant.provider_router import InMemoryProviderHealthStore, ProviderFailureKind
from assistant.types import HermesResponse, Intent, UserContext


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
