from __future__ import annotations

from assistant.contact_book import InMemoryContactBookStore


def test_contact_book_resolves_aliases_and_is_user_scoped() -> None:
    store = InMemoryContactBookStore()
    contact = store.upsert(
        user_id=1,
        name="Илья",
        aliases=["илье", "Илюха"],
        tg_user_id=2002,
        chat_id=3003,
    )

    assert contact.id == 1
    assert store.resolve(1, "илье").chat_id == 3003
    assert store.resolve(1, "ИЛЮХА").tg_user_id == 2002
    assert store.resolve(2, "Илья") is None


def test_contact_book_updates_existing_contact_aliases() -> None:
    store = InMemoryContactBookStore()
    created = store.upsert(user_id=1, name="Илья", aliases=["ilya"], tg_user_id=2002)
    updated = store.upsert(user_id=1, name="Илья", aliases=["илья", "илье"], tg_user_id=2003)

    assert updated.id == created.id
    assert store.resolve(1, "ilya") is None
    assert store.resolve(1, "илье").tg_user_id == 2003
