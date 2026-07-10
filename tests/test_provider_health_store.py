from __future__ import annotations

from datetime import datetime, timedelta, timezone

from assistant.provider_router import ProviderFailureKind
from backend.db import init_db, make_session_factory
from backend.stores import SqlProviderHealthStore


def session_factory(tmp_path):
    factory = make_session_factory(f"sqlite:///{tmp_path / 'test.sqlite3'}")
    init_db(factory)
    return factory


def test_sql_provider_health_persists_success_and_failure(tmp_path) -> None:
    factory = session_factory(tmp_path)
    store_one = SqlProviderHealthStore(factory)
    store_two = SqlProviderHealthStore(factory)
    cooldown = datetime.now(timezone.utc) + timedelta(minutes=5)

    store_one.record_failure(
        "openrouter_free",
        "openrouter/free",
        ProviderFailureKind.RATE_LIMIT,
        latency_ms=900,
        cooldown_until=cooldown,
    )
    store_one.record_success("openrouter_free", "openrouter/free", latency_ms=120)

    health = store_two.get("openrouter_free")
    assert health.model == "openrouter/free"
    assert health.last_success_at is not None
    assert health.last_failure_at is not None
    assert health.latency_ms == 120
    assert health.rate_limit_count == 1
    assert health.cooldown_until is None


def test_sql_provider_health_lists_all_records(tmp_path) -> None:
    factory = session_factory(tmp_path)
    store = SqlProviderHealthStore(factory)
    store.record_success("openrouter_free", "openrouter/free", latency_ms=100)
    store.record_failure("openai_cheap", "gpt-5-nano", ProviderFailureKind.SERVER_ERROR, latency_ms=300)

    assert [item.name for item in store.list_all()] == ["openrouter_free", "openai_cheap"]


def test_sql_provider_health_restores_utc_for_cooldown(tmp_path) -> None:
    factory = session_factory(tmp_path)
    store = SqlProviderHealthStore(factory)
    store.record_failure(
        "openrouter_free",
        "openrouter/free",
        ProviderFailureKind.RATE_LIMIT,
        cooldown_until=datetime.now(timezone.utc) + timedelta(minutes=5),
    )

    health = SqlProviderHealthStore(factory).get("openrouter_free")

    assert health.cooldown_until is not None
    assert health.cooldown_until.tzinfo is not None
    assert health.in_cooldown() is True


def test_sql_provider_health_keeps_rolling_quality_score(tmp_path) -> None:
    factory = session_factory(tmp_path)
    store = SqlProviderHealthStore(factory)

    store.record_success("openrouter_free", "openrouter/free", quality_score=80)
    store.record_failure("openrouter_free", "openrouter/free", ProviderFailureKind.QUALITY)

    health = store.get("openrouter_free")
    assert health.quality_sample_count == 2
    assert health.quality_score == 40
