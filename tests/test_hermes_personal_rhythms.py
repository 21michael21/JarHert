from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

from hermes.native_tools.mcp_api import NativeToolsAPI
from hermes.native_tools.personal_rhythms import PersonalRhythmStore, dispatch_personal_summary


class FakeRhythmAdapter:
    def list_tasks(self, *, list_name: str | None = None) -> str:
        return "Проверить OAuth"

    def list_calendar_events(self, *, when: str) -> str:
        return "15:00 Созвон"

    def complete_task(self, **_payload) -> str:
        return "Готово"

    def move_task(self, **_payload) -> str:
        return "Перенесено"

    def create_task(self, **_payload) -> str:
        raise RuntimeError("Trello timeout")


def make_api(tmp_path: Path) -> NativeToolsAPI:
    return NativeToolsAPI(
        database_path=tmp_path / "personal-os.sqlite3",
        adapter_factory=FakeRhythmAdapter,
    )


def test_daily_brief_uses_factual_personal_today_data(tmp_path: Path) -> None:
    api = make_api(tmp_path)
    api.reminder_create(
        text="позвонить врачу",
        remind_at="2030-01-05T11:00:00+03:00",
        idempotency_key="brief:reminder",
    )

    brief = api.personal_daily_brief(
        now="2030-01-05T10:00:00+03:00",
        timezone_name="Europe/Moscow",
    )

    assert brief["text"].startswith("Сегодня:")
    assert "15:00 Созвон" in brief["text"]
    assert "Проверить OAuth" in brief["text"]
    assert "позвонить врачу" in brief["text"]
    assert brief["data"]["top_three"][0]["title"] == "позвонить врачу"


def test_weekly_review_reports_done_moved_failed_and_next_priorities(tmp_path: Path) -> None:
    api = make_api(tmp_path)
    now = datetime.now(timezone.utc)
    due = (now + timedelta(days=2)).isoformat()
    commitment = api.commitment_create(
        subject="Ответить Илье",
        content="Отправить ревью",
        due_at=due,
        idempotency_key="review:commitment",
    )
    api.commitment_complete(commitment_id=commitment["id"])
    for index in range(3):
        api.commitment_create(
            subject=f"Приоритет {index + 1}",
            content="Сделать на следующей неделе",
            due_at=(now + timedelta(days=8 + index)).isoformat(),
            idempotency_key=f"review:priority:{index}",
        )

    async def confirm(_preview: str) -> bool:
        return True

    actions = [
        {"type": "task.done", "payload": {"title": "Закрыть баг"}},
        {"type": "task.move", "payload": {"title": "Документация", "target_list": "Next"}},
        {"type": "task.create", "payload": {"title": "Упавшая задача"}},
    ]
    asyncio.run(
        api.action_plan_confirm_execute(
            actions=actions,
            idempotency_key="review:actions",
            confirmer=confirm,
        )
    )

    review = api.personal_weekly_review(now=now.isoformat(), timezone_name="UTC")

    assert review["completed"][0]["title"] == "Закрыть баг"
    assert review["moved"][0]["title"] == "Документация"
    assert review["stuck"][0]["title"] == "Упавшая задача"
    assert [item["title"] for item in review["top_three"]] == [
        "Приоритет 1",
        "Приоритет 2",
        "Приоритет 3",
    ]
    assert "За неделю:" in review["text"]


def test_periodic_summary_delivery_is_idempotent(tmp_path: Path) -> None:
    store = PersonalRhythmStore(tmp_path / "personal-os.sqlite3")
    sent: list[tuple[int, str]] = []

    first = dispatch_personal_summary(
        store,
        lambda: "Сегодня:\nГлавное: проверить OAuth",
        lambda chat_id, text: sent.append((chat_id, text)) or "telegram:10",
        chat_id=566055009,
        summary_type="daily",
        period_key="2030-01-05",
    )
    replay = dispatch_personal_summary(
        store,
        lambda: "не должно собираться повторно",
        lambda chat_id, text: sent.append((chat_id, text)) or "telegram:11",
        chat_id=566055009,
        summary_type="daily",
        period_key="2030-01-05",
    )

    assert first == {"status": "sent", "external_id": "telegram:10"}
    assert replay == {"status": "already_sent", "external_id": "telegram:10"}
    assert sent == [(566055009, "Сегодня:\nГлавное: проверить OAuth")]


def test_hermes_profile_exposes_daily_and_weekly_rhythms() -> None:
    root = Path(__file__).resolve().parents[1]
    config = (root / "hermes" / "config.yaml").read_text(encoding="utf-8")
    skill = (root / "hermes" / "skills" / "personal-operating-center" / "SKILL.md").read_text(
        encoding="utf-8"
    )

    assert "- personal_daily_brief" in config
    assert "- personal_weekly_review" in config
    assert "dispatch_personal_summary.py" in skill
