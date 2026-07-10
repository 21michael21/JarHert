from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from hermes.native_tools.contacts import ContactStore, ContactStoreError


def make_store(tmp_path) -> ContactStore:
    return ContactStore(tmp_path / "personal-os.sqlite3")


def test_contact_alias_resolves_to_one_telegram_target(tmp_path) -> None:
    store = make_store(tmp_path)
    contact = store.add_contact(
        name="Илья",
        telegram_chat_id=123456,
        aliases=["Ильюха", "Илье"],
    )

    resolved = store.resolve_contact("ильюха")

    assert resolved.id == contact.id
    assert resolved.telegram_chat_id == 123456


def test_duplicate_alias_for_another_contact_is_rejected(tmp_path) -> None:
    store = make_store(tmp_path)
    store.add_contact(name="Илья", telegram_chat_id=1, aliases=["Илюха"])

    with pytest.raises(ContactStoreError, match="уже используется"):
        store.add_contact(name="Игорь", telegram_chat_id=2, aliases=["Илюха"])


def test_message_plan_approves_all_items_once(tmp_path) -> None:
    store = make_store(tmp_path)
    store.add_contact(name="Илья", telegram_chat_id=123, aliases=[])
    store.add_contact(name="Маша", telegram_chat_id=456, aliases=[])
    tomorrow = datetime.now(timezone.utc) + timedelta(days=1)

    plan = store.create_message_plan(
        [
            {"contact": "Илья", "text": "Пришли отчёт", "send_at": tomorrow.isoformat()},
            {"contact": "Маша", "text": "Созвон в 15:00", "send_at": tomorrow.isoformat()},
        ],
        idempotency_key="telegram-update-77",
    )

    assert plan.status == "draft"
    assert len(plan.messages) == 2
    approved = store.approve_message_plan(plan.id)
    assert approved.status == "scheduled"
    assert {item.status for item in approved.messages} == {"scheduled"}


def test_replayed_update_returns_existing_message_plan(tmp_path) -> None:
    store = make_store(tmp_path)
    store.add_contact(name="Илья", telegram_chat_id=123, aliases=[])
    item = {"contact": "Илья", "text": "Проверить задачу", "send_at": "2030-01-01T12:00:00+03:00"}

    first = store.create_message_plan([item], idempotency_key="update-99")
    replay = store.create_message_plan([item], idempotency_key="update-99")

    assert replay.id == first.id
    assert store.count_message_plans() == 1


def test_due_messages_exclude_drafts_and_future_items(tmp_path) -> None:
    store = make_store(tmp_path)
    store.add_contact(name="Илья", telegram_chat_id=123, aliases=[])
    now = datetime(2030, 1, 1, 12, 0, tzinfo=timezone.utc)
    due = store.create_message_plan(
        [{"contact": "Илья", "text": "Уже пора", "send_at": (now - timedelta(minutes=1)).isoformat()}],
        idempotency_key="due",
    )
    store.approve_message_plan(due.id)
    future = store.create_message_plan(
        [{"contact": "Илья", "text": "Ещё рано", "send_at": (now + timedelta(hours=1)).isoformat()}],
        idempotency_key="future",
    )
    store.approve_message_plan(future.id)
    store.create_message_plan(
        [{"contact": "Илья", "text": "Не подтверждено", "send_at": (now - timedelta(minutes=1)).isoformat()}],
        idempotency_key="draft",
    )

    messages = store.claim_due_messages(now=now, limit=10)

    assert [item.text for item in messages] == ["Уже пора"]
    assert messages[0].status == "sending"


def test_delivery_result_is_persisted(tmp_path) -> None:
    store = make_store(tmp_path)
    store.add_contact(name="Илья", telegram_chat_id=123, aliases=[])
    plan = store.create_message_plan(
        [{"contact": "Илья", "text": "Готово", "send_at": "2030-01-01T12:00:00+00:00"}],
        idempotency_key="delivery",
    )
    store.approve_message_plan(plan.id)
    message = store.claim_due_messages(now=datetime(2030, 1, 1, 12, 1, tzinfo=timezone.utc))[0]

    store.mark_message_sent(message.id, external_id="telegram:42")

    updated = store.get_message(message.id)
    assert updated.status == "sent"
    assert updated.external_id == "telegram:42"
    assert updated.sent_at is not None

