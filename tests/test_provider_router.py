from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from assistant.hermes_client import HermesClientError
from assistant.provider_policy import InMemoryProviderBudgetLedger, ProviderSelectionPolicy, require_policy_controlled_transport
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


def spec(
    name: str,
    priority: int,
    *,
    cost_mode: ProviderCostMode = ProviderCostMode.FREE,
    supports_json: bool = True,
    capabilities: frozenset[str] = frozenset({"chat"}),
    timeout_seconds: float = 5,
    estimated_cost_micro_usd: int = 0,
) -> ProviderSpec:
    return ProviderSpec(
        name=name,
        model=f"{name}-model",
        cost_mode=cost_mode,
        timeout_seconds=timeout_seconds,
        max_tokens=300,
        supports_json=supports_json,
        priority=priority,
        kind=ProviderKind.OPENAI_CHAT,
        credential_env="TEST_KEY",
        base_url="https://example.test/v1",
        capabilities=capabilities,
        estimated_cost_micro_usd=estimated_cost_micro_usd,
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


def test_provider_attempt_events_keep_trace_and_never_include_prompt() -> None:
    registry = ProviderRegistry([spec("primary", 10)])
    events = []
    router = ProviderRouterClient(
        registry=registry,
        health_store=InMemoryProviderHealthStore(),
        client_factory=lambda _provider: FakeClient(
            [HermesResponse(text="Ок.", provider="primary", model="primary-model", latency_ms=123)]
        ),
        event_logger=lambda request, event_type, meta: events.append((request.trace_id, event_type, meta)),
    )

    router.ask(replace(request(), prompt="личный текст не должен попасть в event", trace_id="trace-provider"))

    assert events[0] == ("trace-provider", "provider_attempt_started", {"provider": "primary", "model": "primary-model", "attempt": 1})
    assert events[1] == (
        "trace-provider",
        "provider_attempt_succeeded",
        {"provider": "primary", "model": "primary-model", "latency_ms": 123},
    )


def test_free_only_never_calls_cheap_or_paid_provider_after_free_failure() -> None:
    registry = ProviderRegistry(
        [
            spec("free", 10),
            spec("cheap", 20, cost_mode=ProviderCostMode.CHEAP, estimated_cost_micro_usd=100),
            spec("paid", 30, cost_mode=ProviderCostMode.PAID, estimated_cost_micro_usd=1_000),
        ]
    )
    free = FakeClient([HermesClientError("HTTP 429", status_code=429)])
    cheap = FakeClient([HermesResponse(text="cheap answer")])
    paid = FakeClient([HermesResponse(text="paid answer")])
    router = ProviderRouterClient(
        registry=registry,
        health_store=InMemoryProviderHealthStore(),
        client_factory=lambda provider: {"free": free, "cheap": cheap, "paid": paid}[provider.name],
        policy=ProviderSelectionPolicy(cost_mode="free_only", deadline_seconds=5, max_attempts=3),
    )

    with pytest.raises(HermesClientError):
        router.ask(request())

    assert free.calls == 1
    assert cheap.calls == 0
    assert paid.calls == 0


def test_provider_router_requires_json_support_for_structured_request() -> None:
    registry = ProviderRegistry(
        [
            spec("plain", 10, supports_json=False),
            spec("json", 20, supports_json=True),
        ]
    )
    plain = FakeClient([HermesResponse(text="not called")])
    json_client = FakeClient([HermesResponse(text='{"actions": []}', provider="json")])
    router = ProviderRouterClient(
        registry=registry,
        health_store=InMemoryProviderHealthStore(),
        client_factory=lambda provider: {"plain": plain, "json": json_client}[provider.name],
        policy=ProviderSelectionPolicy(cost_mode="free_only", deadline_seconds=5, max_attempts=2),
    )

    response = router.ask(replace(request(), context={"response_format": "json"}))

    assert response.provider == "json"
    assert plain.calls == 0
    assert json_client.calls == 1


def test_provider_router_requires_declared_capability() -> None:
    registry = ProviderRegistry(
        [
            spec("chat", 10, capabilities=frozenset({"chat"})),
            spec("monitor", 20, capabilities=frozenset({"chat", "monitor"})),
        ]
    )
    chat = FakeClient([HermesResponse(text="not called")])
    monitor = FakeClient([HermesResponse(text="trigger", provider="monitor")])
    router = ProviderRouterClient(
        registry=registry,
        health_store=InMemoryProviderHealthStore(),
        client_factory=lambda provider: {"chat": chat, "monitor": monitor}[provider.name],
        policy=ProviderSelectionPolicy(cost_mode="free_only", deadline_seconds=5, max_attempts=2),
    )

    response = router.ask(replace(request(), context={"capability": "monitor"}))

    assert response.provider == "monitor"
    assert chat.calls == 0
    assert monitor.calls == 1


def test_provider_router_uses_quality_then_latency_when_prices_match() -> None:
    registry = ProviderRegistry([spec("slow", 10), spec("fast", 20)])
    health = InMemoryProviderHealthStore()
    for _ in range(4):
        health.record_success("slow", "slow-model", latency_ms=900, quality_score=90)
        health.record_success("fast", "fast-model", latency_ms=120, quality_score=90)
    slow = FakeClient([HermesResponse(text="slow")])
    fast = FakeClient([HermesResponse(text="fast", provider="fast")])
    router = ProviderRouterClient(
        registry=registry,
        health_store=health,
        client_factory=lambda provider: {"slow": slow, "fast": fast}[provider.name],
        policy=ProviderSelectionPolicy(cost_mode="balanced", deadline_seconds=5, max_attempts=2),
    )

    assert router.ask(request()).provider == "fast"
    assert slow.calls == 0
    assert fast.calls == 1


def test_provider_router_enforces_estimated_daily_budget_before_transport() -> None:
    registry = ProviderRegistry(
        [spec("cheap", 10, cost_mode=ProviderCostMode.CHEAP, estimated_cost_micro_usd=100)]
    )
    client = FakeClient([HermesResponse(text="first"), HermesResponse(text="second")])
    router = ProviderRouterClient(
        registry=registry,
        health_store=InMemoryProviderHealthStore(),
        client_factory=lambda _provider: client,
        policy=ProviderSelectionPolicy(
            cost_mode="cheap",
            deadline_seconds=5,
            max_attempts=1,
            daily_budget_micro_usd=100,
            budget_ledger=InMemoryProviderBudgetLedger(),
        ),
    )

    assert router.ask(request()).text == "first"
    with pytest.raises(HermesClientError, match="budget"):
        router.ask(request())
    assert client.calls == 1


def test_cheap_mode_prefers_cheap_provider_and_keeps_free_as_fallback() -> None:
    registry = ProviderRegistry(
        [
            spec("free", 10),
            spec("cheap", 20, cost_mode=ProviderCostMode.CHEAP, estimated_cost_micro_usd=100),
        ]
    )
    free = FakeClient([HermesResponse(text="free answer", provider="free")])
    cheap = FakeClient([HermesResponse(text="cheap answer", provider="cheap")])
    router = ProviderRouterClient(
        registry=registry,
        health_store=InMemoryProviderHealthStore(),
        client_factory=lambda provider: {"free": free, "cheap": cheap}[provider.name],
        policy=ProviderSelectionPolicy(
            cost_mode="cheap",
            deadline_seconds=5,
            max_attempts=2,
            daily_budget_micro_usd=1_000,
        ),
    )

    response = router.ask(request())

    assert response.provider == "cheap"
    assert cheap.calls == 1
    assert free.calls == 0


def test_balanced_policy_skips_repeatedly_low_quality_free_provider_for_cheap_provider() -> None:
    registry = ProviderRegistry(
        [
            spec("free", 10),
            spec("cheap", 20, cost_mode=ProviderCostMode.CHEAP, estimated_cost_micro_usd=100),
        ]
    )
    health = InMemoryProviderHealthStore()
    for _ in range(3):
        health.record_failure("free", "free-model", ProviderFailureKind.QUALITY)
        health.record_success("cheap", "cheap-model", quality_score=100)
    free = FakeClient([HermesResponse(text="not called")])
    cheap = FakeClient([HermesResponse(text="cheap", provider="cheap")])
    router = ProviderRouterClient(
        registry=registry,
        health_store=health,
        client_factory=lambda provider: {"free": free, "cheap": cheap}[provider.name],
        policy=ProviderSelectionPolicy(
            cost_mode="balanced",
            deadline_seconds=5,
            max_attempts=2,
            daily_budget_micro_usd=100,
        ),
    )

    assert router.ask(request()).provider == "cheap"
    assert free.calls == 0
    assert cheap.calls == 1


def test_provider_router_clamps_transport_timeout_and_max_attempts() -> None:
    registry = ProviderRegistry([spec("one", 10, timeout_seconds=10), spec("two", 20), spec("three", 30)])
    received_timeouts: list[float] = []
    one = FakeClient([HermesClientError("temporary")])
    two = FakeClient([HermesClientError("temporary")])
    three = FakeClient([HermesResponse(text="not called")])

    def factory(provider: ProviderSpec):
        received_timeouts.append(provider.timeout_seconds)
        return {"one": one, "two": two, "three": three}[provider.name]

    router = ProviderRouterClient(
        registry=registry,
        health_store=InMemoryProviderHealthStore(),
        client_factory=factory,
        policy=ProviderSelectionPolicy(cost_mode="free_only", deadline_seconds=1, max_attempts=2),
    )

    with pytest.raises(HermesClientError):
        router.ask(request())

    assert received_timeouts[0] <= 1
    assert one.calls == 1
    assert two.calls == 1
    assert three.calls == 0


def test_free_only_rejects_opaque_direct_transport_modes() -> None:
    with pytest.raises(ValueError, match="provider_router"):
        require_policy_controlled_transport(cost_mode="free_only", hermes_mode="cli")

    require_policy_controlled_transport(cost_mode="free_only", hermes_mode="provider_router")
