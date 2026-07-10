from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import pytest

from hermes.native_tools.telegram_text_export import (
    ExportMessage,
    TelegramExportError,
    TelegramTextExporter,
    normalize_peer,
)


class FakeClient:
    def __init__(self, messages, *, accessible=True) -> None:
        self.messages = messages
        self.accessible = accessible

    async def resolve_peer(self, peer):
        return {"id": 123, "title": "Тестовый чат", "peer": peer}

    async def is_accessible_dialog(self, entity):
        return self.accessible

    async def iter_text_messages(self, entity, *, limit):
        for message in self.messages[:limit]:
            yield message


def message(number: int, text: str) -> ExportMessage:
    return ExportMessage(
        message_id=number,
        date=datetime(2030, 1, number, 12, tzinfo=timezone.utc),
        sender_id=100 + number,
        sender_name=f"User {number}",
        text=text,
        reply_to_message_id=None,
    )


def test_txt_export_contains_only_text_records(tmp_path) -> None:
    exporter = TelegramTextExporter(output_dir=tmp_path)

    result = asyncio.run(
        exporter.export(FakeClient([message(1, "Привет"), message(2, "Второе")]), peer="@test_chat", output_format="txt")
    )

    assert result.message_count == 2
    assert result.path.suffix == ".txt"
    content = result.path.read_text(encoding="utf-8")
    assert "Привет" in content
    assert "User 2" in content
    assert not list(tmp_path.glob("*.part"))


def test_jsonl_export_has_stable_schema(tmp_path) -> None:
    exporter = TelegramTextExporter(output_dir=tmp_path)

    result = asyncio.run(
        exporter.export(FakeClient([message(1, "Привет")]), peer="-100123", output_format="jsonl")
    )
    row = json.loads(result.path.read_text(encoding="utf-8"))

    assert row == {
        "date": "2030-01-01T12:00:00+00:00",
        "id": 1,
        "reply_to_message_id": None,
        "sender_id": 101,
        "sender_name": "User 1",
        "text": "Привет",
    }


def test_chat_must_exist_in_user_dialogs(tmp_path) -> None:
    exporter = TelegramTextExporter(output_dir=tmp_path)

    with pytest.raises(TelegramExportError, match="нет среди диалогов"):
        asyncio.run(exporter.export(FakeClient([], accessible=False), peer="@public", output_format="txt"))


@pytest.mark.parametrize("peer", ["https://t.me/test", "../../etc", "", "+79990000000"])
def test_peer_accepts_only_username_or_numeric_id(peer) -> None:
    with pytest.raises(TelegramExportError):
        normalize_peer(peer)


def test_export_stops_at_size_cap_without_partial_file(tmp_path) -> None:
    exporter = TelegramTextExporter(output_dir=tmp_path, max_output_bytes=1024)

    result = asyncio.run(
        exporter.export(
            FakeClient([message(1, "A" * 800), message(2, "B" * 800)]),
            peer="@test_chat",
            output_format="txt",
        )
    )

    assert result.truncated is True
    assert result.path.stat().st_size <= 1024
    assert not list(tmp_path.glob("*.part"))


def test_limit_is_bounded(tmp_path) -> None:
    exporter = TelegramTextExporter(output_dir=tmp_path)

    with pytest.raises(TelegramExportError, match="50000"):
        asyncio.run(exporter.export(FakeClient([]), peer="@test_chat", output_format="txt", limit=50_001))
