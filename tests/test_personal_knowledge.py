from __future__ import annotations

from datetime import datetime, timedelta, timezone

from assistant.personal_knowledge import InMemoryPersonalKnowledgeStore


def test_personal_knowledge_crud_search_history_and_retention() -> None:
    store = InMemoryPersonalKnowledgeStore(default_retention_days=7)
    created = store.create(
        user_id=1,
        text="OAuth токен нужно обновить",
        note_type="memory",
        source="telegram",
        project="JarHert",
        contact="Илья",
        now=datetime(2026, 7, 1, tzinfo=timezone.utc),
    )

    assert created.id == 1
    assert created.project == "JarHert"
    assert store.search(1, "oauth")[0].id == created.id

    updated = store.update(1, created.id, text="OAuth токен обновить до деплоя")
    assert updated is not None
    assert updated.text == "OAuth токен обновить до деплоя"

    history = store.history_for_note(1, created.id)
    assert [item.action for item in history] == ["create", "update"]
    assert history[1].before_text == "OAuth токен нужно обновить"

    assert store.delete(1, created.id)
    assert store.list_for_user(1) == []
    assert store.history_for_note(1, created.id)[-1].action == "delete"

    expired = store.create(
        user_id=1,
        text="устаревшая заметка",
        note_type="note",
        source="telegram",
        now=datetime(2026, 7, 1, tzinfo=timezone.utc),
    )
    purged = store.purge_expired(now=datetime(2026, 7, 10, tzinfo=timezone.utc))

    assert purged == 1
    assert store.get(1, expired.id) is None


def test_personal_knowledge_is_user_scoped() -> None:
    store = InMemoryPersonalKnowledgeStore()
    note = store.create(user_id=1, text="личная заметка")

    assert store.get(2, note.id) is None
    assert store.search(2, "личная") == []
    assert not store.delete(2, note.id)
