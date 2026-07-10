from __future__ import annotations

from datetime import datetime, timezone

from hermes.native_tools.contacts import ContactStore
from hermes.native_tools.delivery import dispatch_due_messages


def test_dispatch_sends_due_message_to_resolved_contact(tmp_path) -> None:
    store = ContactStore(tmp_path / "personal-os.sqlite3")
    store.add_contact(name="Илья", telegram_chat_id=123, aliases=[])
    plan = store.create_message_plan(
        [{"contact": "Илья", "text": "Пора созвониться", "send_at": "2030-01-01T12:00:00+00:00"}],
        idempotency_key="dispatch",
    )
    store.approve_message_plan(plan.id)
    sent: list[tuple[int, str]] = []

    result = dispatch_due_messages(
        store,
        lambda chat_id, text: sent.append((chat_id, text)) or "telegram:77",
        now=datetime(2030, 1, 1, 12, 1, tzinfo=timezone.utc),
    )

    assert result == {"claimed": 1, "sent": 1, "failed": 0}
    assert sent == [(123, "Пора созвониться")]
    assert store.get_message(plan.messages[0].id).status == "sent"
