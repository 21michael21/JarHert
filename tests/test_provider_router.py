from __future__ import annotations

from datetime import datetime, timedelta, timezone

from assistant.hermes_client import HermesClientError
from assistant.provider_registry import ProviderCostMode, ProviderKind, ProviderRegistry, ProviderSpec
from assistant.provider_router import InMemoryProviderHealthStore, ProviderFailureKind, ProviderRouterClient
from assistant.types import HermesRequest, HermesResponse, Intent, UserContext


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def ask(self, request):
        self.calls += 1
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def spec(name: str, priority: int) -> ProviderSpec:
    return ProviderSpec(
        name=name,
        model=f"{name}-model",
        cost_mode=ProviderCostMode.FREE,
        timeout_seconds=5,
        max_tokens=300,
        supports_json=True,
        priority=priority,
        kind=ProviderKind.OPENAI_CHAT,
        credential_env="TEST_KEY",
        base_url="https://example.test/v1",
    )


def request() -> HermesRequest:
    return HermesRequest(
        user=UserContext(user_id=1, tg_user_id=1001),
        prompt="коротко ответь",
        intent=Intent.ASK,
    )


def test_provider_router_falls_back_and_cools_down_rate_limited_provider() -> None:
    registry = ProviderRegistry([spec("primary", 10), spec("fallback", 20)])
    health = InMemoryProviderHealthStore()
    primary = FakeClient([HermesClientError("HTTP 429 rate limit", status_code=429)])
    fallback = FakeClient([HermesResponse(text="Готово.", provider="fallback", model="fallback-model")])
    router = ProviderRouterClient(
        registry=registry,
        health_store=health,
        client_factory=lambda provider: {"primary": primary, "fallback": fallback}[provider.name],
    )

    response = router.ask(request())

    primary_health = health.get("primary")
    assert response.text == "Готово."
    assert response.fallback_count == 1
    assert primary_health.rate_limit_count == 1
    assert primary_health.cooldown_until is not None
    assert primary.calls == 1
    assert fallback.calls == 1


def test_provider_router_skips_provider_in_cooldown() -> None:
    registry = ProviderRegistry([spec("primary", 10), spec("fallback", 20)])
    health = InMemoryProviderHealthStore()
    health.record_failure(
        "primary",
        "primary-model",
        ProviderFailureKind.RATE_LIMIT,
        latency_ms=10,
        cooldown_until=datetime.now(timezone.utc) + timedelta(minutes=2),
    )
    primary = FakeClient([HermesResponse(text="should not be called")])
    fallback = FakeClient([HermesResponse(text="Ответ.", provider="fallback", model="fallback-model")])
    router = ProviderRouterClient(
        registry=registry,
        health_store=health,
        client_factory=lambda provider: {"primary": primary, "fallback": fallback}[provider.name],
    )

    response = router.ask(request())

    assert response.text == "Ответ."
    assert primary.calls == 0
    assert fallback.calls == 1


def test_provider_router_falls_back_on_bad_quality_response() -> None:
    registry = ProviderRegistry([spec("primary", 10), spec("fallback", 20)])
    health = InMemoryProviderHealthStore()
    primary = FakeClient([HermesResponse(text="Я как ИИ не могу иметь мнение.", provider="primary", model="bad")])
    fallback = FakeClient([HermesResponse(text="Короткий ответ.", provider="fallback", model="ok")])
    router = ProviderRouterClient(
        registry=registry,
        health_store=health,
        client_factory=lambda provider: {"primary": primary, "fallback": fallback}[provider.name],
    )

    response = router.ask(request())

    assert response.text == "Короткий ответ."
    assert response.fallback_count == 1
    assert health.get("primary").quality_error_count == 1


def test_provider_router_records_success_latency() -> None:
    registry = ProviderRegistry([spec("primary", 10)])
    health = InMemoryProviderHealthStore()
    primary = FakeClient([HermesResponse(text="Ок.", provider="primary", model="primary-model", latency_ms=123)])
    router = ProviderRouterClient(
        registry=registry,
        health_store=health,
        client_factory=lambda provider: primary,
    )

    router.ask(request())

    provider_health = health.get("primary")
    assert provider_health.last_success_at is not None
    assert provider_health.latency_ms == 123
