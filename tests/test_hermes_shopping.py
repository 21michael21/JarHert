from __future__ import annotations

from pathlib import Path

from hermes.native_tools.mcp_api import NativeToolsAPI


def test_shopping_add_deduplicates_active_items_and_marks_bought(tmp_path) -> None:
    api = NativeToolsAPI(database_path=tmp_path / "personal-os.sqlite3")

    milk = api.shopping_add(
        text="Молоко",
        category="Продукты",
        quantity="2 л",
        project="Личное",
        idempotency_key="telegram:100:1",
    )
    replay = api.shopping_add(
        text="Молоко",
        category="Продукты",
        quantity="2 л",
        project="Личное",
        idempotency_key="telegram:100:1",
    )
    same_item = api.shopping_add(text="  молоко  ", project="Личное", idempotency_key="telegram:100:2")

    assert replay == milk
    assert same_item["id"] == milk["id"]
    assert api.shopping_list(project="Личное")["items"] == [milk]

    bought = api.shopping_mark_bought(item_id=milk["id"])

    assert bought["status"] == "bought"
    assert api.shopping_list(project="Личное")["items"] == []
    assert api.shopping_list(status="bought")["items"] == [bought]


def test_shopping_remove_is_soft_and_does_not_delete_another_item(tmp_path) -> None:
    api = NativeToolsAPI(database_path=tmp_path / "personal-os.sqlite3")
    first = api.shopping_add(text="Батарейки", idempotency_key="telegram:100:3")
    second = api.shopping_add(text="Лампочка", idempotency_key="telegram:100:4")

    removed = api.shopping_remove(item_id=first["id"])

    assert removed["status"] == "cancelled"
    assert api.shopping_list()["items"] == [second]
    assert api.shopping_list(status="cancelled")["items"] == [removed]


def test_shopping_is_exposed_in_the_profile_and_skill(tmp_path) -> None:
    api = NativeToolsAPI(database_path=tmp_path / "personal-os.sqlite3")
    assert api.shopping_list() == {"items": []}

    root = Path(__file__).resolve().parents[1]
    config = (root / "hermes" / "config.yaml").read_text(encoding="utf-8")
    skill = (root / "hermes" / "skills" / "shopping" / "SKILL.md").read_text(encoding="utf-8")
    assert "- shopping_add" in config
    assert "- shopping_list" in config
    assert "- shopping_mark_bought" in config
    assert "- shopping_remove" in config
    assert "mcp_jarhert_native_shopping_add" in skill
