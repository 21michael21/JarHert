from __future__ import annotations

from pathlib import Path

from hermes.native_tools.mcp_api import NativeToolsAPI


def test_trip_keeps_route_booking_document_and_due_reminder_together(tmp_path) -> None:
    api = NativeToolsAPI(database_path=tmp_path / "personal-os.sqlite3")

    trip = api.trip_create(
        name="Амстердам",
        destination="Нидерланды",
        starts_at="2030-05-10T10:00:00+03:00",
        ends_at="2030-05-14T18:00:00+03:00",
        idempotency_key="telegram:trip:1",
    )
    booking = api.trip_add_item(
        trip_id=trip["id"],
        kind="booking",
        title="Отель",
        details="Номер брони хранится в почте",
        due_at="2030-05-01T12:00:00+03:00",
        idempotency_key="telegram:trip:2",
    )
    document = api.trip_add_item(
        trip_id=trip["id"],
        kind="document",
        title="Паспорт",
        idempotency_key="telegram:trip:3",
    )

    details = api.trip_details(trip_id=trip["id"])

    assert [item["kind"] for item in details["items"]] == ["booking", "document"]
    assert details["trip"] == trip
    assert booking["due_at"] == "2030-05-01T09:00:00+00:00"
    reminders = api.reminder_list()["items"]
    assert len(reminders) == 1
    assert reminders[0]["source_type"] == "trip_item"
    assert reminders[0]["source_id"] == booking["id"]

    completed = api.trip_item_complete(item_id=document["id"])
    assert completed["status"] == "done"


def test_cancelling_trip_is_soft_and_cancels_its_pending_item_reminders(tmp_path) -> None:
    api = NativeToolsAPI(database_path=tmp_path / "personal-os.sqlite3")
    trip = api.trip_create(name="Казань", destination="Россия", idempotency_key="telegram:trip:4")
    item = api.trip_add_item(
        trip_id=trip["id"],
        kind="checklist",
        title="Оформить страховку",
        due_at="2030-06-01T10:00:00+03:00",
        idempotency_key="telegram:trip:5",
    )

    cancelled = api.trip_cancel(trip_id=trip["id"])

    assert cancelled["status"] == "cancelled"
    assert api.trip_list()["items"] == []
    assert api.reminder_list() == {"items": []}
    assert api.trip_details(trip_id=trip["id"])["items"][0]["id"] == item["id"]


def test_trip_tools_are_exposed_in_profile_and_skill(tmp_path) -> None:
    api = NativeToolsAPI(database_path=tmp_path / "personal-os.sqlite3")
    assert api.trip_list() == {"items": []}

    root = Path(__file__).resolve().parents[1]
    config = (root / "hermes" / "config.yaml").read_text(encoding="utf-8")
    skill = (root / "hermes" / "skills" / "trips" / "SKILL.md").read_text(encoding="utf-8")
    for tool in ("trip_create", "trip_list", "trip_details", "trip_add_item", "trip_item_complete", "trip_cancel_confirmed"):
        assert f"- {tool}" in config
    assert "mcp_jarhert_native_trip_create" in skill
