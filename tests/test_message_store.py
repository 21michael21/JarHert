from __future__ import annotations

from datetime import datetime, timezone

from backend.db import init_db, make_session_factory
from backend.message_store import SqlCollectedMessageStore


def test_collected_message_store_deduplicates_telegram_message_id(tmp_path) -> None:
    factory = make_session_factory(f"sqlite:///{tmp_path / 'messages.sqlite3'}")
    init_db(factory)
    store = SqlCollectedMessageStore(factory)
    timestamp = datetime.now(timezone.utc)

    first = store.add_message(
        chat_id=-1001,
        chat_title="Новости",
        sender_id=101,
        sender_name="Sender",
        text="первое сообщение",
        timestamp=timestamp,
        telegram_message_id=55,
    )
    second = store.add_message(
        chat_id=-1001,
        chat_title="Новости",
        sender_id=101,
        sender_name="Sender",
        text="дубликат",
        timestamp=timestamp,
        telegram_message_id=55,
    )

    assert second.id == first.id
    assert store.count_unprocessed() == 1


def test_collected_message_store_marks_processed(tmp_path) -> None:
    factory = make_session_factory(f"sqlite:///{tmp_path / 'messages.sqlite3'}")
    init_db(factory)
    store = SqlCollectedMessageStore(factory)
    message = store.add_message(
        chat_id=-1001,
        chat_title="Чат",
        sender_id=None,
        sender_name=None,
        text="обсуждают новый релиз",
        timestamp=datetime.now(timezone.utc),
        telegram_message_id=1,
    )

    assert len(store.list_unprocessed()) == 1
    assert store.mark_processed([message.id]) == 1
    assert store.list_unprocessed() == []
