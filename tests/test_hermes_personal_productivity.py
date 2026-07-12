from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

from hermes.native_tools.mcp_api import NativeToolsAPI
from hermes.native_tools.contacts import ContactStore
from hermes.native_tools.delivery import dispatch_due_messages, dispatch_due_reminders
from hermes.native_tools.personal_productivity import PersonalProductivityStore
from hermes.native_tools.personal_crm import PersonalCRMStore


ROOT = Path(__file__).resolve().parents[1]


class FakeDailyAdapter:
    def list_tasks(self, *, list_name: str | None = None) -> str:
        assert list_name == "Today"
        return "Проверить OAuth"

    def list_calendar_events(self, *, when: str) -> str:
        assert when == "today"
        return "15:00 Созвон по ML"


def test_reminder_crud_is_idempotent_and_supports_recurrence(tmp_path: Path) -> None:
    api = NativeToolsAPI(database_path=tmp_path / "personal-os.sqlite3")

    created = api.reminder_create(
        text="заниматься ML",
        remind_at="2030-01-05T12:00:00+03:00",
        recurrence=None,
        idempotency_key="telegram:566055009:100",
    )
    replay = api.reminder_create(
        text="этот повтор не должен изменить запись",
        remind_at="2031-01-01T12:00:00+03:00",
        recurrence="daily",
        idempotency_key="telegram:566055009:100",
    )

    assert replay == created
    assert api.reminder_list() == {"items": [created]}

    moved = api.reminder_reschedule(
        reminder_id=created["id"],
        remind_at="2030-01-09T19:00:00+03:00",
        recurrence="weekly",
    )
    assert moved["remind_at"] == "2030-01-09T16:00:00+00:00"
    assert moved["recurrence"] == "weekly"

    moved_again = api.reminder_reschedule(
        reminder_id=created["id"],
        remind_at="2030-01-16T19:00:00+03:00",
    )
    assert moved_again["recurrence"] == "weekly"

    cancelled = api.reminder_cancel(reminder_id=created["id"])
    assert cancelled["status"] == "cancelled"
    assert api.reminder_list() == {"items": []}


def test_commitment_due_date_creates_one_reminder_and_completion_cancels_it(tmp_path: Path) -> None:
    api = NativeToolsAPI(database_path=tmp_path / "personal-os.sqlite3")

    created = api.commitment_create(
        subject="Ответить Илье",
        content="Отправить результаты ревью",
        contact="Илья",
        project="Hub_ML",
        due_at="2030-01-05T12:00:00+03:00",
        idempotency_key="telegram:566055009:101:commitment",
    )
    replay = api.commitment_create(
        subject="Дубль",
        content="Не должен сохраниться",
        due_at="2031-01-05T12:00:00+03:00",
        idempotency_key="telegram:566055009:101:commitment",
    )

    assert replay == created
    reminders = api.reminder_list()["items"]
    assert len(reminders) == 1
    assert reminders[0]["text"] == "Срок обещания: Ответить Илье — Отправить результаты ревью"
    assert reminders[0]["source_type"] == "commitment"
    assert reminders[0]["source_id"] == created["id"]

    api.commitment_complete(commitment_id=created["id"])

    assert api.reminder_list() == {"items": []}


def test_crm_timeline_keeps_agreements_and_next_contact_without_duplicates(tmp_path: Path) -> None:
    api = NativeToolsAPI(database_path=tmp_path / "personal-os.sqlite3")

    created = api.crm_interaction_log(
        contact="Илья",
        kind="agreement",
        summary="Договорились проверить OAuth",
        project="Hub_ML",
        occurred_at="2030-01-05T12:00:00+03:00",
        next_contact_at="2030-01-09T19:00:00+03:00",
        idempotency_key="telegram:566055009:102:crm",
    )
    replay = api.crm_interaction_log(
        contact="Другой контакт",
        kind="note",
        summary="Не должно сохраниться",
        idempotency_key="telegram:566055009:102:crm",
    )

    assert replay == created
    assert created["occurred_at"] == "2030-01-05T09:00:00+00:00"
    assert created["next_contact_at"] == "2030-01-09T16:00:00+00:00"
    assert api.crm_timeline(contact="Илья") == {"items": [created]}
    assert api.crm_timeline(contact="Другой контакт") == {"items": []}
    followup_reminders = api.reminder_list()["items"]
    assert len(followup_reminders) == 1
    assert followup_reminders[0]["source_type"] == "crm_interaction"


def test_personal_today_combines_sources_and_selects_three_local_priorities(tmp_path: Path) -> None:
    api = NativeToolsAPI(
        database_path=tmp_path / "personal-os.sqlite3",
        adapter_factory=FakeDailyAdapter,
    )
    api.reminder_create(
        text="позвонить врачу",
        remind_at="2030-01-05T11:00:00+03:00",
        idempotency_key="today:reminder",
    )
    commitment = api.commitment_create(
        subject="Ответить Илье",
        content="Отправить ревью",
        contact="Илья",
        due_at="2030-01-05T13:00:00+03:00",
        idempotency_key="today:commitment",
    )
    api.crm_interaction_log(
        contact="Анна",
        kind="meeting",
        summary="Обсудили запуск",
        next_contact_at="2030-01-05T14:00:00+03:00",
        idempotency_key="today:crm",
    )

    overview = api.personal_today(
        now="2030-01-05T10:00:00+03:00",
        timezone_name="Europe/Moscow",
    )

    assert overview["tasks"] == "Проверить OAuth"
    assert overview["calendar"] == "15:00 Созвон по ML"
    assert [item["text"] for item in overview["reminders"]] == ["позвонить врачу"]
    assert [item["id"] for item in overview["commitments"]] == [commitment["id"]]
    assert [item["contact"] for item in overview["followups"]] == ["Анна"]
    assert [item["type"] for item in overview["top_three"]] == [
        "reminder",
        "commitment",
        "followup",
    ]


def test_due_reminders_are_delivered_once_and_recurring_item_advances(tmp_path: Path) -> None:
    store = PersonalProductivityStore(tmp_path / "personal-os.sqlite3")
    store.create_reminder(
        text="разовое",
        remind_at="2030-01-05T09:00:00+00:00",
        idempotency_key="delivery:once",
    )
    recurring = store.create_reminder(
        text="читать",
        remind_at="2030-01-05T09:00:00+00:00",
        recurrence="daily",
        idempotency_key="delivery:daily",
    )
    sent: list[tuple[int, str]] = []

    result = dispatch_due_reminders(
        store,
        lambda chat_id, text: sent.append((chat_id, text)) or "telegram:1",
        chat_id=566055009,
        now="2030-01-05T10:00:00+00:00",
    )
    replay = dispatch_due_reminders(
        store,
        lambda chat_id, text: sent.append((chat_id, text)) or "telegram:2",
        chat_id=566055009,
        now="2030-01-05T10:00:00+00:00",
    )

    assert result == {"claimed": 2, "sent": 2, "failed": 0}
    assert replay == {"claimed": 0, "sent": 0, "failed": 0}
    assert sent == [(566055009, "разовое"), (566055009, "читать")]
    active = store.list_reminders()
    assert [(item.id, item.remind_at) for item in active] == [
        (recurring.id, "2030-01-06T09:00:00+00:00")
    ]


def test_hermes_profile_exposes_productivity_tools_and_natural_workflows() -> None:
    config = (ROOT / "hermes" / "config.yaml").read_text(encoding="utf-8")
    soul = (ROOT / "hermes" / "SOUL.md").read_text(encoding="utf-8")
    voice_skill = (ROOT / "hermes" / "skills" / "voice-inbox" / "SKILL.md").read_text(encoding="utf-8")
    skill = (ROOT / "hermes" / "skills" / "personal-operating-center" / "SKILL.md").read_text(
        encoding="utf-8"
    )

    for tool in (
        "reminder_create",
        "reminder_list",
        "reminder_reschedule",
        "reminder_cancel",
        "crm_interaction_log",
        "crm_timeline",
        "personal_today",
    ):
        assert f"- {tool}" in config
    assert "что у меня сегодня" in soul.lower()
    assert "model: small" in config
    assert "не проси пользователя переписывать голосовое" in soul.lower()
    assert "голосовой черновик" in soul.lower()
    assert "не проси пользователя переписывать голосовое" in voice_skill.lower()
    assert "голосовой черновик" in voice_skill.lower()
    assert "разгрузи голову" in skill.lower()
    assert "выбери три" in skill.lower()


def test_brain_dump_plan_creates_reminder_once_without_nested_idempotency_key(tmp_path: Path) -> None:
    api = NativeToolsAPI(database_path=tmp_path / "personal-os.sqlite3")

    async def confirm(_preview: str) -> bool:
        return True

    actions = [
        {
            "type": "reminder.create",
            "payload": {
                "text": "проверить OAuth",
                "remind_at": "2030-01-05T12:00:00+03:00",
            },
        }
    ]
    first = asyncio.run(
        api.action_plan_confirm_execute(
            actions=actions,
            idempotency_key="telegram:566055009:brain-dump",
            confirmer=confirm,
        )
    )
    replay = asyncio.run(
        api.action_plan_confirm_execute(
            actions=actions,
            idempotency_key="telegram:566055009:brain-dump",
            confirmer=confirm,
        )
    )

    assert first == replay
    assert first["status"] == "succeeded"
    assert len(api.reminder_list()["items"]) == 1


def test_successful_scheduled_message_is_added_to_crm_timeline_once(tmp_path: Path) -> None:
    database_path = tmp_path / "personal-os.sqlite3"
    contacts = ContactStore(database_path)
    productivity = PersonalProductivityStore(database_path)
    crm = PersonalCRMStore(database_path)
    contacts.add_contact(name="Илья", telegram_chat_id=123, aliases=[])
    plan = contacts.create_message_plan(
        [{"contact": "Илья", "text": "Проверил OAuth", "send_at": "2030-01-05T09:00:00+00:00"}],
        idempotency_key="telegram:message:1",
    )
    contacts.approve_message_plan(plan.id)

    def log_sent(message, _external_id) -> None:
        crm.log_interaction(
            contact=message.contact_name,
            kind="message",
            summary=message.text,
            occurred_at="2030-01-05T10:00:00+00:00",
            idempotency_key=f"scheduled-message:{message.id}",
        )

    dispatch_due_messages(
        contacts,
        lambda _chat_id, _text: "telegram:42",
        now="2030-01-05T10:00:00+00:00",
        on_sent=log_sent,
    )
    dispatch_due_messages(
        contacts,
        lambda _chat_id, _text: "telegram:43",
        now="2030-01-05T10:00:00+00:00",
        on_sent=log_sent,
    )

    timeline = crm.list_interactions(contact="Илья")
    assert [(item.kind, item.summary) for item in timeline] == [("message", "Проверил OAuth")]


def test_existing_personal_os_database_gains_commitment_idempotency_column(tmp_path: Path) -> None:
    database_path = tmp_path / "personal-os.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE commitments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                subject TEXT NOT NULL,
                content TEXT NOT NULL,
                contact TEXT,
                project_key TEXT,
                due_at TEXT,
                status TEXT NOT NULL DEFAULT 'open',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                completed_at TEXT
            )
            """
        )

    api = NativeToolsAPI(database_path=database_path)
    created = api.commitment_create(
        subject="Проверить миграцию",
        content="Старая база продолжает работать",
        idempotency_key="legacy:commitment:1",
    )
    replay = api.commitment_create(
        subject="Дубль",
        content="Не сохранять",
        idempotency_key="legacy:commitment:1",
    )

    assert replay == created
