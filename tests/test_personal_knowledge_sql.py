from __future__ import annotations

from backend.db import init_db, make_session_factory
from backend.stores import SqlPersonalKnowledgeStore, UserStore


def session_factory(tmp_path):
    factory = make_session_factory(f"sqlite:///{tmp_path / 'knowledge.sqlite3'}")
    init_db(factory)
    return factory


def test_sql_personal_knowledge_persists_searches_and_keeps_history(tmp_path) -> None:
    factory = session_factory(tmp_path)
    users = UserStore(factory)
    user_one = users.get_or_create(8101)
    user_two = users.get_or_create(8102)
    store = SqlPersonalKnowledgeStore(factory)

    created = store.create(
        user_id=user_one.id,
        text="OAuth refresh token проверить",
        note_type="memory",
        source="telegram",
        project="JarHert",
        contact="Илья",
    )
    updated = store.update(user_one.id, created.id, text="OAuth refresh token проверить сегодня")

    assert updated is not None
    assert store.search(user_one.id, "oauth")[0].id == created.id
    assert store.search(user_two.id, "oauth") == []
    assert [item.action for item in store.history_for_note(user_one.id, created.id)] == ["create", "update"]
    assert store.delete(user_one.id, created.id)
    assert store.list_for_user(user_one.id) == []
