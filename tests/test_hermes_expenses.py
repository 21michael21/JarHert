from __future__ import annotations

from pathlib import Path

from hermes.native_tools.expenses import ExpenseStore
from hermes.native_tools.mcp_api import NativeToolsAPI


def test_expense_add_is_idempotent_and_validated(tmp_path: Path) -> None:
    store = ExpenseStore(tmp_path / "personal.sqlite3")

    first = store.add(text="AWS", amount=12000, category="infra", idempotency_key="tg:1")
    replay = store.add(text="AWS", amount=12000, category="infra", idempotency_key="tg:1")

    assert replay.id == first.id
    assert len(store.list()) == 1

    for bad_amount in (0, -5, 10**12):
        try:
            store.add(text="X", amount=bad_amount, idempotency_key=f"bad:{bad_amount}")
        except ValueError as error:
            assert "Сумма" in str(error)
        else:  # pragma: no cover
            raise AssertionError(f"amount {bad_amount} accepted")


def test_expense_monthly_totals_group_by_currency_and_category(tmp_path: Path) -> None:
    store = ExpenseStore(tmp_path / "personal.sqlite3")
    store.add(text="AWS", amount=100, currency="USD", category="infra", spent_at="2030-01-05T10:00:00", idempotency_key="a")
    store.add(text="DO", amount=50, currency="USD", category="infra", spent_at="2030-01-06T10:00:00", idempotency_key="b")
    store.add(text="Кофе", amount=300, currency="RUB", category="food", spent_at="2030-01-06T11:00:00", idempotency_key="c")
    store.add(text="Старый месяц", amount=999, currency="RUB", spent_at="2029-12-01T10:00:00", idempotency_key="d")

    totals = store.monthly_totals(month="2030-01")

    by_pair = {(item["currency"], item["category"]): item for item in totals["items"]}
    assert by_pair[("USD", "infra")]["total"] == 150.0
    assert by_pair[("USD", "infra")]["count"] == 2
    assert by_pair[("RUB", "food")]["total"] == 300.0
    assert all(item["total"] != 999.0 for item in totals["items"])


def test_native_api_expense_round_trip(tmp_path: Path) -> None:
    api = NativeToolsAPI(database_path=tmp_path / "personal.sqlite3")

    added = api.expense_add(text="AWS", amount=12000, category="infra", idempotency_key="tg:add:1")
    listed = api.expense_list()
    monthly = api.expense_monthly()

    assert added["amount"] == 12000
    assert listed["items"][0]["text"] == "AWS"
    assert monthly["items"]


def test_expense_tools_registered_in_catalog_and_config() -> None:
    from hermes.native_tools.tool_catalog import TOOL_CATALOG, configured_tool_names

    names = {spec.name for spec in TOOL_CATALOG}
    assert {"expense_add", "expense_list", "expense_monthly"} <= names
    configured = configured_tool_names(Path(__file__).parents[1] / "hermes" / "config.yaml")
    assert {"expense_add", "expense_list", "expense_monthly"} <= configured
