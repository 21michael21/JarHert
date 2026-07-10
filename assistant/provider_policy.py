from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime, timezone
from enum import Enum
from typing import Protocol

from assistant.provider_registry import ProviderCostMode, ProviderRegistry, ProviderSpec
from assistant.types import HermesRequest


class AiCostMode(str, Enum):
    FREE_ONLY = "free_only"
    CHEAP = "cheap"
    BALANCED = "balanced"


class ProviderBudgetLedger(Protocol):
    def reserve(
        self,
        *,
        provider_name: str,
        estimated_cost_micro_usd: int,
        daily_budget_micro_usd: int,
        now: datetime | None = None,
    ) -> bool: ...


@dataclass(frozen=True)
class BudgetLedgerEntry:
    day: date
    provider_name: str
    estimated_cost_micro_usd: int


@dataclass(frozen=True)
class BudgetLedgerSummary:
    day: date
    estimated_cost_micro_usd: int = 0
    request_count: int = 0


@dataclass
class InMemoryProviderBudgetLedger:
    _spent_by_day: dict[date, int] = field(default_factory=dict)
    _request_counts_by_day: dict[date, int] = field(default_factory=dict)
    entries: list[BudgetLedgerEntry] = field(default_factory=list)

    def reserve(
        self,
        *,
        provider_name: str,
        estimated_cost_micro_usd: int,
        daily_budget_micro_usd: int,
        now: datetime | None = None,
    ) -> bool:
        current_day = (now or datetime.now(timezone.utc)).date()
        spent = self._spent_by_day.get(current_day, 0)
        cost = max(0, estimated_cost_micro_usd)
        if cost and spent + cost > max(0, daily_budget_micro_usd):
            return False
        self._spent_by_day[current_day] = spent + cost
        self._request_counts_by_day[current_day] = self._request_counts_by_day.get(current_day, 0) + 1
        self.entries.append(BudgetLedgerEntry(current_day, provider_name, cost))
        return True

    def summary(self, *, now: datetime | None = None) -> BudgetLedgerSummary:
        current_day = (now or datetime.now(timezone.utc)).date()
        return BudgetLedgerSummary(
            day=current_day,
            estimated_cost_micro_usd=self._spent_by_day.get(current_day, 0),
            request_count=self._request_counts_by_day.get(current_day, 0),
        )


@dataclass(frozen=True)
class ProviderSelection:
    providers: list[ProviderSpec]
    skipped: list[str]


class ProviderSelectionPolicy:
    def __init__(
        self,
        *,
        cost_mode: str | AiCostMode,
        deadline_seconds: float,
        max_attempts: int,
        cooldown_seconds: int = 120,
        daily_budget_micro_usd: int = 0,
        minimum_quality_score: int = 60,
        minimum_quality_samples: int = 3,
        budget_ledger: ProviderBudgetLedger | None = None,
    ) -> None:
        self.cost_mode = _parse_cost_mode(cost_mode)
        self.deadline_seconds = max(0.1, deadline_seconds)
        self.max_attempts = max(1, max_attempts)
        self.cooldown_seconds = max(0, cooldown_seconds)
        self.daily_budget_micro_usd = max(0, daily_budget_micro_usd)
        self.minimum_quality_score = min(100, max(0, minimum_quality_score))
        self.minimum_quality_samples = max(1, minimum_quality_samples)
        self.budget_ledger = budget_ledger or InMemoryProviderBudgetLedger()

    def select(
        self,
        *,
        registry: ProviderRegistry,
        health_store,
        request: HermesRequest,
        remaining_seconds: float,
        now: datetime | None = None,
    ) -> ProviderSelection:
        if remaining_seconds <= 0:
            return ProviderSelection([], ["deadline_exceeded"])

        current = now or datetime.now(timezone.utc)
        requires_json = request.context.get("response_format") == "json"
        capability = request.context.get("capability", "chat").strip() or "chat"
        ranked: list[tuple[tuple[int, ...], ProviderSpec]] = []
        skipped: list[str] = []
        for provider in registry.enabled():
            if not _is_allowed_by_cost_mode(provider.cost_mode, self.cost_mode):
                skipped.append(f"{provider.name}: cost_mode")
                continue
            if capability not in provider.capabilities:
                skipped.append(f"{provider.name}: capability")
                continue
            if requires_json and not provider.supports_json:
                skipped.append(f"{provider.name}: json")
                continue

            health = health_store.get(provider.name)
            if health.in_cooldown(now=current):
                skipped.append(f"{provider.name}: cooldown")
                continue
            if (
                health.quality_sample_count >= self.minimum_quality_samples
                and health.quality_score < self.minimum_quality_score
            ):
                skipped.append(f"{provider.name}: quality")
                continue

            effective_timeout = min(provider.timeout_seconds, max(0.1, remaining_seconds))
            candidate = replace(provider, timeout_seconds=effective_timeout)
            ranked.append((_rank(candidate, health, self.cost_mode), candidate))

        ranked.sort(key=lambda item: item[0])
        return ProviderSelection([provider for _, provider in ranked], skipped)

    def reserve_budget(self, provider: ProviderSpec, *, now: datetime | None = None) -> bool:
        return self.budget_ledger.reserve(
            provider_name=provider.name,
            estimated_cost_micro_usd=provider.estimated_cost_micro_usd,
            daily_budget_micro_usd=self.daily_budget_micro_usd,
            now=now,
        )


def _parse_cost_mode(value: str | AiCostMode) -> AiCostMode:
    try:
        return AiCostMode(value)
    except ValueError as error:
        valid = ", ".join(item.value for item in AiCostMode)
        raise ValueError(f"AI_COST_MODE must be one of: {valid}") from error


def require_policy_controlled_transport(*, cost_mode: str | AiCostMode, hermes_mode: str) -> None:
    if _parse_cost_mode(cost_mode) != AiCostMode.FREE_ONLY:
        return
    if hermes_mode in {"cli", "http"}:
        raise ValueError(
            "AI_COST_MODE=free_only requires HERMES_MODE=provider_router or cli_router; "
            "opaque direct transports cannot prove their model cost."
        )


def _is_allowed_by_cost_mode(provider_cost: ProviderCostMode, cost_mode: AiCostMode) -> bool:
    if cost_mode == AiCostMode.FREE_ONLY:
        return provider_cost in {ProviderCostMode.FREE, ProviderCostMode.LOCAL}
    if cost_mode == AiCostMode.CHEAP:
        return provider_cost in {ProviderCostMode.FREE, ProviderCostMode.LOCAL, ProviderCostMode.CHEAP}
    return True


def _rank(provider: ProviderSpec, health, cost_mode: AiCostMode) -> tuple[int, ...]:
    quality_score = health.quality_score if health.quality_sample_count else 100
    latency_ms = health.latency_ms if health.latency_ms is not None else int(provider.timeout_seconds * 1_000)
    price = provider.estimated_cost_micro_usd
    if cost_mode == AiCostMode.BALANCED:
        return (-quality_score, latency_ms, price, provider.priority)
    if cost_mode == AiCostMode.CHEAP:
        reliability_tier = {
            ProviderCostMode.LOCAL: 0,
            ProviderCostMode.CHEAP: 1,
            ProviderCostMode.FREE: 2,
            ProviderCostMode.PAID: 3,
        }[provider.cost_mode]
        return (reliability_tier, -quality_score, latency_ms, price, provider.priority)
    return (price, -quality_score, latency_ms, provider.priority)
