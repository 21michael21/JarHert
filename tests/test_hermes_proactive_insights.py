from __future__ import annotations

from pathlib import Path

from hermes.native_tools.mcp_api import NativeToolsAPI
from hermes.native_tools.personal_rhythms import format_daily_brief


class _EmptyAdapter:
    def list_tasks(self, *, list_name: str | None = None) -> str:
        return ""

    def list_calendar_events(self, *, when: str = "today") -> str:
        return ""


def _seed(api: NativeToolsAPI) -> None:
    api._personal_os().create_commitment(
        subject="Илья",
        content="Прислать оффер",
        due_at="2020-01-01T09:00:00+03:00",
    )
    api._personal_os().create_commitment(
        subject="Катя",
        content="Созвон по онбордингу",
        due_at="2999-01-01T09:00:00+03:00",
    )
    trip = api._trips().create(name="Казань", destination="Казань", starts_at="2999-01-05T10:00:00+03:00", idempotency_key="t1")
    api._trips().add_item(trip_id=trip.id, kind="checklist", title="Купить билет", idempotency_key="ti1")


def test_personal_today_surfaces_overdue_and_upcoming_insights(tmp_path: Path) -> None:
    api = NativeToolsAPI(database_path=tmp_path / "personal.sqlite3", adapter_factory=_EmptyAdapter)
    _seed(api)
    api._subscriptions().create(name="VPN", amount=10, currency="USD", cadence="monthly", next_charge_at="2999-01-02T00:00:00+00:00", idempotency_key="s1")

    data = api.personal_today(now="2999-01-01T12:00:00+03:00")

    insights = data["insights"]
    assert any("Просрочено обещание: Илья" in item for item in insights)
    assert not any("Катя" in item for item in insights)  # далеко за горизонтом
    assert any("Списание VPN" in item for item in insights)
    assert any("Поездка Казань" in item and "открытых пунктов 1" in item for item in insights)


def test_daily_brief_includes_insights_section(tmp_path: Path) -> None:
    api = NativeToolsAPI(database_path=tmp_path / "personal.sqlite3", adapter_factory=_EmptyAdapter)
    _seed(api)

    text = format_daily_brief(api.personal_today(now="2999-01-01T12:00:00+03:00"))

    assert "Не забудь:" in text
    assert "Просрочено обещание: Илья" in text


def test_daily_brief_without_insights_keeps_old_shape(tmp_path: Path) -> None:
    api = NativeToolsAPI(database_path=tmp_path / "personal.sqlite3", adapter_factory=_EmptyAdapter)

    text = format_daily_brief(api.personal_today(now="2999-01-01T12:00:00+03:00"))

    assert "Не забудь:" not in text
