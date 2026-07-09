from __future__ import annotations

from telegram_collector.config import load_chat_refs


def test_load_chat_refs_from_csv() -> None:
    assert load_chat_refs(raw="@one, -100123 , two", file_path="") == ["@one", "-100123", "two"]


def test_load_chat_refs_from_json_file(tmp_path) -> None:
    config_path = tmp_path / "chats.json"
    config_path.write_text('{"chats": ["@one", "-100123"]}', encoding="utf-8")

    assert load_chat_refs(raw="", file_path=str(config_path)) == ["@one", "-100123"]
