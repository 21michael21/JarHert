from __future__ import annotations

from backend.db import init_db, make_session_factory
from backend.stores import SqlContactBookStore, UserStore


def session_factory(tmp_path):
    factory = make_session_factory(f"sqlite:///{tmp_path / 'contacts.sqlite3'}")
    init_db(factory)
    return factory


def test_sql_contact_book_persists_aliases_and_telegram_identifiers(tmp_path) -> None:
    factory = session_factory(tmp_path)
    users = UserStore(factory)
    user_one = users.get_or_create(8201)
    user_two = users.get_or_create(8202)
    store = SqlContactBookStore(factory)

    contact = store.upsert(
        user_id=user_one.id,
        name="Илья",
        aliases=["илье", "ilya"],
        tg_user_id=2002,
        chat_id=3003,
    )

    assert store.resolve(user_one.id, "илье").id == contact.id
    assert store.resolve(user_one.id, "ilya").chat_id == 3003
    assert store.resolve(user_two.id, "илье") is None
