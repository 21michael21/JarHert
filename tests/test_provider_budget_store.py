from __future__ import annotations

from datetime import datetime, timezone

from backend.db import init_db, make_session_factory
from backend.provider_budget_store import SqlProviderBudgetLedger


def test_sql_provider_budget_ledger_reserves_once_and_keeps_audit_entry(tmp_path) -> None:
    factory = make_session_factory(f"sqlite:///{tmp_path / 'budget.sqlite3'}")
    init_db(factory)
    ledger = SqlProviderBudgetLedger(factory)
    now = datetime(2026, 7, 9, tzinfo=timezone.utc)

    assert ledger.reserve(
        provider_name="openai_cheap",
        estimated_cost_micro_usd=100,
        daily_budget_micro_usd=150,
        now=now,
    )
    assert not ledger.reserve(
        provider_name="openai_cheap",
        estimated_cost_micro_usd=100,
        daily_budget_micro_usd=150,
        now=now,
    )

    summary = ledger.summary(now=now)
    assert summary.estimated_cost_micro_usd == 100
    assert summary.request_count == 1
    assert [(entry.provider_name, entry.estimated_cost_micro_usd) for entry in ledger.entries(now=now)] == [
        ("openai_cheap", 100)
    ]
