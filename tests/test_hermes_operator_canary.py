from __future__ import annotations

from datetime import datetime, timezone

import pytest

from hermes.native_tools.operator_canary import OperatorCanaryError, run_operator_canary


class FakeAdapter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def create_task(self, *, title: str, **_kwargs: object) -> str:
        self.calls.append(("task.create", title))
        return "created"

    def delete_task(self, *, title: str) -> str:
        self.calls.append(("task.delete", title))
        return "deleted"

    def create_calendar_event(self, *, title: str, **_kwargs: object) -> str:
        self.calls.append(("calendar.create", title))
        return "created"

    def delete_calendar_event(self, *, title: str) -> str:
        self.calls.append(("calendar.delete", title))
        return "deleted"


class FakeApi:
    def __init__(self) -> None:
        self.reminder_id = 73
        self.cancelled: list[int] = []

    def reminder_create(self, **_kwargs: object) -> dict[str, int]:
        return {"id": self.reminder_id}

    def reminder_cancel(self, *, reminder_id: int) -> dict[str, object]:
        self.cancelled.append(reminder_id)
        return {"id": reminder_id, "status": "cancelled"}


def test_operator_canary_creates_delivers_and_cleans_every_temporary_resource() -> None:
    adapter = FakeAdapter()
    api = FakeApi()
    sent: list[tuple[int, str]] = []

    result = run_operator_canary(
        api=api,
        adapter=adapter,
        sender=lambda chat_id, text: sent.append((chat_id, text)) or "telegram:1",
        chat_id=566055009,
        run_id="abc123",
        now=datetime(2030, 1, 5, 9, tzinfo=timezone.utc),
    )

    assert result["ok"] is True
    assert result["run_id"] == "abc123"
    assert result["telegram_sent"] is True
    assert api.cancelled == [73]
    assert [name for name, _title in adapter.calls] == [
        "task.create",
        "calendar.create",
        "calendar.delete",
        "task.delete",
    ]
    assert sent[0][0] == 566055009
    assert "abc123" in sent[0][1]


def test_operator_canary_cleans_created_resources_when_delivery_fails() -> None:
    adapter = FakeAdapter()
    api = FakeApi()

    with pytest.raises(OperatorCanaryError, match="Telegram delivery failed"):
        run_operator_canary(
            api=api,
            adapter=adapter,
            sender=lambda _chat_id, _text: (_ for _ in ()).throw(RuntimeError("delivery unavailable")),
            chat_id=566055009,
            run_id="abc123",
            now=datetime(2030, 1, 5, 9, tzinfo=timezone.utc),
        )

    assert api.cancelled == [73]
    assert [name for name, _title in adapter.calls] == [
        "task.create",
        "calendar.create",
        "calendar.delete",
        "task.delete",
    ]


def test_operator_canary_reports_first_integration_failure_without_cleanup_crash() -> None:
    class FailingAdapter(FakeAdapter):
        def create_task(self, *, title: str, **_kwargs: object) -> str:
            self.calls.append(("task.create", title))
            raise RuntimeError("Trello unavailable")

    adapter = FailingAdapter()

    with pytest.raises(OperatorCanaryError, match="Integration canary failed: RuntimeError"):
        run_operator_canary(
            api=FakeApi(),
            adapter=adapter,
            sender=lambda _chat_id, _text: "telegram:1",
            chat_id=566055009,
            run_id="abc123",
            now=datetime(2030, 1, 5, 9, tzinfo=timezone.utc),
        )

    assert adapter.calls == [("task.create", "[JarHert canary abc123]")]
