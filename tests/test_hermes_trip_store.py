from __future__ import annotations

import pytest

from hermes.native_tools.trips import TripStore


def test_trip_store_keeps_entries_and_soft_cancels_open_work(tmp_path) -> None:
    store = TripStore(tmp_path / "personal-os.sqlite3")
    trip = store.create(
        name="Амстердам",
        destination="Нидерланды",
        starts_at="2030-05-10T10:00:00+03:00",
        ends_at="2030-05-14T18:00:00+03:00",
        idempotency_key="trip:1",
    )
    replay = store.create(name="дубль", destination="другое", idempotency_key="trip:1")
    booking = store.add_item(
        trip_id=trip.id,
        kind="booking",
        title="Отель",
        due_at="2030-05-01T12:00:00+03:00",
        idempotency_key="trip:2",
    )
    document = store.add_item(
        trip_id=trip.id,
        kind="document",
        title="Паспорт",
        idempotency_key="trip:3",
    )

    assert replay == trip
    assert booking.due_at == "2030-05-01T09:00:00+00:00"
    assert store.complete_item(document.id).status == "done"
    assert store.cancel(trip.id).status == "cancelled"
    assert [item.status for item in store.list_items(trip.id)] == ["cancelled", "done"]
    assert store.list() == []


def test_trip_store_rejects_invalid_dates_and_writes_to_cancelled_trip(tmp_path) -> None:
    store = TripStore(tmp_path / "personal-os.sqlite3")
    with pytest.raises(ValueError, match="Окончание"):
        store.create(
            name="Плохая дата",
            destination="Тест",
            starts_at="2030-05-10T10:00:00+03:00",
            ends_at="2030-05-10T09:00:00+03:00",
            idempotency_key="trip:bad",
        )

    trip = store.create(name="Тест", destination="Тест", idempotency_key="trip:ok")
    store.cancel(trip.id)
    with pytest.raises(ValueError, match="неактивную"):
        store.add_item(trip_id=trip.id, kind="route", title="Поздно", idempotency_key="trip:late")
