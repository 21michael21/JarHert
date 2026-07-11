from __future__ import annotations

from pathlib import Path

from hermes.native_tools.mcp_api import NativeToolsAPI


def test_subscription_crud_updates_reminder_and_monthly_total(tmp_path: Path) -> None:
    synced: list[list[dict[str, object]]] = []
    api = NativeToolsAPI(
        database_path=tmp_path / "personal-os.sqlite3",
        subscription_sync=lambda rows: synced.append(rows),
    )

    created = api.subscription_create(
        name="GitHub",
        amount="12.00",
        currency="USD",
        cadence="monthly",
        next_charge_at="2030-01-10T10:00:00+03:00",
        category="tools",
        idempotency_key="telegram:subscription:1",
    )
    replay = api.subscription_create(
        name="Дубль",
        amount="99",
        currency="EUR",
        cadence="yearly",
        next_charge_at="2031-01-10T10:00:00+03:00",
        idempotency_key="telegram:subscription:1",
    )

    assert replay == created
    assert api.subscription_list()["monthly_totals"] == {"USD": "12.00"}
    reminders = api.reminder_list()["items"]
    assert reminders[0]["text"] == "Списание GitHub: 12.00 USD"

    updated = api.subscription_update(
        subscription_id=created["id"],
        amount="24.00",
        next_charge_at="2030-02-10T10:00:00+03:00",
    )
    assert updated["amount"] == "24.00"
    assert api.reminder_list()["items"][0]["remind_at"] == "2030-02-10T07:00:00+00:00"

    cancelled = api.subscription_cancel(subscription_id=created["id"])
    assert cancelled["status"] == "cancelled"
    assert api.reminder_list() == {"items": []}
    assert len(synced) == 3
