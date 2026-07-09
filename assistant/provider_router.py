from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Callable

from assistant.provider_clients import HermesClient
from assistant.provider_diagnostics import HermesClientError
from assistant.provider_registry import ProviderRegistry, ProviderSpec
from assistant.quality_gates import check_output
from assistant.types import HermesRequest, HermesResponse


class ProviderFailureKind(str, Enum):
    AUTH = "auth"
    RATE_LIMIT = "rate_limit"
    SERVER_ERROR = "server_error"
    TIMEOUT = "timeout"
    QUALITY = "quality"
    OTHER = "other"


@dataclass(frozen=True)
class ProviderHealth:
    name: str
    model: str = ""
    last_success_at: datetime | None = None
    last_failure_at: datetime | None = None
    latency_ms: int | None = None
    auth_error_count: int = 0
    rate_limit_count: int = 0
    server_error_count: int = 0
    timeout_count: int = 0
    quality_error_count: int = 0
    other_error_count: int = 0
    cooldown_until: datetime | None = None
    updated_at: datetime | None = None

    def in_cooldown(self, *, now: datetime | None = None) -> bool:
        current = now or datetime.now(timezone.utc)
        return self.cooldown_until is not None and self.cooldown_until > current


class InMemoryProviderHealthStore:
    def __init__(self) -> None:
        self._items: dict[str, ProviderHealth] = {}

    def get(self, name: str) -> ProviderHealth:
        return self._items.get(name) or ProviderHealth(name=name)

    def list_all(self) -> list[ProviderHealth]:
        return list(self._items.values())

    def record_success(self, name: str, model: str, *, latency_ms: int | None = None) -> ProviderHealth:
        now = datetime.now(timezone.utc)
        current = self.get(name)
        updated = replace(
            current,
            model=model,
            last_success_at=now,
            latency_ms=latency_ms,
            cooldown_until=None,
            updated_at=now,
        )
        self._items[name] = updated
        return updated

    def record_failure(
        self,
        name: str,
        model: str,
        failure_kind: ProviderFailureKind,
        *,
        latency_ms: int | None = None,
        cooldown_until: datetime | None = None,
    ) -> ProviderHealth:
        now = datetime.now(timezone.utc)
        current = self.get(name)
        values = {
            "model": model,
            "last_failure_at": now,
            "latency_ms": latency_ms,
            "cooldown_until": cooldown_until,
            "updated_at": now,
        }
        counter = _counter_field(failure_kind)
        values[counter] = getattr(current, counter) + 1
        updated = replace(current, **values)
        self._items[name] = updated
        return updated


ClientFactory = Callable[[ProviderSpec], HermesClient]


class ProviderRouterClient:
    def __init__(
        self,
        *,
        registry: ProviderRegistry,
        health_store,
        client_factory: ClientFactory,
        cooldown_seconds: int = 120,
    ) -> None:
        self.registry = registry
        self.health_store = health_store
        self.client_factory = client_factory
        self.cooldown_seconds = cooldown_seconds

    def ask(self, request: HermesRequest) -> HermesResponse:
        failures: list[str] = []
        attempted = 0
        now = datetime.now(timezone.utc)
        for provider in self.registry.enabled():
            health = self.health_store.get(provider.name)
            if health.in_cooldown(now=now):
                failures.append(f"{provider.name}: cooldown")
                continue
            attempted += 1
            client = self.client_factory(provider)
            try:
                response = client.ask(request)
            except Exception as exc:
                failure_kind = classify_provider_failure(exc)
                cooldown_until = _cooldown_until(failure_kind, seconds=self.cooldown_seconds)
                self.health_store.record_failure(
                    provider.name,
                    provider.model,
                    failure_kind,
                    latency_ms=_latency_from_error(exc),
                    cooldown_until=cooldown_until,
                )
                failures.append(f"{provider.name}: {failure_kind.value}")
                continue

            output_gate = check_output(response.text)
            if not output_gate.ok:
                self.health_store.record_failure(
                    provider.name,
                    provider.model,
                    ProviderFailureKind.QUALITY,
                    latency_ms=response.latency_ms,
                    cooldown_until=None,
                )
                failures.append(f"{provider.name}: {output_gate.reason}")
                continue

            self.health_store.record_success(provider.name, provider.model, latency_ms=response.latency_ms)
            return HermesResponse(
                text=response.text,
                provider=provider.name,
                model=response.model or provider.model,
                latency_ms=response.latency_ms,
                fallback_count=max(0, attempted - 1),
                fallback_reason="; ".join(failures[-3:]) if failures else response.fallback_reason,
                diagnostics=response.diagnostics,
            )

        raise HermesClientError("; ".join(failures) or "all providers unavailable")


def classify_provider_failure(error: Exception) -> ProviderFailureKind:
    status_code = getattr(error, "status_code", None)
    if status_code in {401, 403}:
        return ProviderFailureKind.AUTH
    if status_code == 429:
        return ProviderFailureKind.RATE_LIMIT
    if isinstance(status_code, int) and status_code >= 500:
        return ProviderFailureKind.SERVER_ERROR

    lowered = str(error).lower()
    if "401" in lowered or "403" in lowered or "invalid api key" in lowered:
        return ProviderFailureKind.AUTH
    if "429" in lowered or "rate limit" in lowered or "rate_limit" in lowered:
        return ProviderFailureKind.RATE_LIMIT
    if "timeout" in lowered or "timed out" in lowered:
        return ProviderFailureKind.TIMEOUT
    if any(marker in lowered for marker in ("500", "502", "503", "server error", "tempor")):
        return ProviderFailureKind.SERVER_ERROR
    return ProviderFailureKind.OTHER


def _cooldown_until(failure_kind: ProviderFailureKind, *, seconds: int) -> datetime | None:
    if failure_kind in {
        ProviderFailureKind.AUTH,
        ProviderFailureKind.RATE_LIMIT,
        ProviderFailureKind.SERVER_ERROR,
        ProviderFailureKind.TIMEOUT,
    }:
        return datetime.now(timezone.utc) + timedelta(seconds=seconds)
    return None


def _counter_field(failure_kind: ProviderFailureKind) -> str:
    return {
        ProviderFailureKind.AUTH: "auth_error_count",
        ProviderFailureKind.RATE_LIMIT: "rate_limit_count",
        ProviderFailureKind.SERVER_ERROR: "server_error_count",
        ProviderFailureKind.TIMEOUT: "timeout_count",
        ProviderFailureKind.QUALITY: "quality_error_count",
        ProviderFailureKind.OTHER: "other_error_count",
    }[failure_kind]


def _latency_from_error(error: Exception) -> int | None:
    value = getattr(error, "latency_ms", None)
    return value if isinstance(value, int) else None
