from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from hermes.native_tools.telegram_text_export import (
    ExportMessage,
    TelegramExportError,
    TelegramTextExporter,
    cleanup_expired_exports,
    normalize_peer,
    read_export_for_analysis,
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
    assert result.expires_at > datetime.now(timezone.utc) + timedelta(hours=47)


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


def test_cleanup_removes_only_expired_telegram_exports(tmp_path) -> None:
    now = datetime(2030, 1, 3, 12, tzinfo=timezone.utc)
    old_export = tmp_path / "chat_20300101_110000.txt"
    fresh_export = tmp_path / "chat_20300103_110000.jsonl"
    unrelated = tmp_path / "keep-me.txt"
    linked_target = tmp_path / "outside.txt"
    linked_export = tmp_path / "linked_20300101_110000.txt"
    for path in (old_export, fresh_export, unrelated):
        path.write_text("text", encoding="utf-8")
    linked_target.write_text("must remain", encoding="utf-8")
    linked_export.symlink_to(linked_target)
    old_timestamp = (now - timedelta(hours=49)).timestamp()
    fresh_timestamp = (now - timedelta(hours=1)).timestamp()
    os.utime(old_export, (old_timestamp, old_timestamp))
    os.utime(fresh_export, (fresh_timestamp, fresh_timestamp))

    removed = cleanup_expired_exports(tmp_path, now=now)

    assert removed == 1
    assert not old_export.exists()
    assert fresh_export.exists()
    assert unrelated.exists()
    assert linked_export.is_symlink()
    assert linked_target.read_text(encoding="utf-8") == "must remain"


def test_export_cleanup_timer_is_daily_and_uses_the_profile_export_directory() -> None:
    root = Path(__file__).resolve().parents[1]
    service = (root / "deploy" / "vps" / "systemd" / "hermes-telegram-export-cleanup.service").read_text(encoding="utf-8")
    timer = (root / "deploy" / "vps" / "systemd" / "hermes-telegram-export-cleanup.timer").read_text(encoding="utf-8")

    assert "cleanup_telegram_exports.py" in service
    assert "HERMES_HOME=%h/.hermes/profiles/jarhert" in service
    assert "OnCalendar=*-*-* 03:35:00" in timer


def test_export_can_be_read_for_explicit_analysis_but_not_outside_its_directory(tmp_path) -> None:
    exporter = TelegramTextExporter(output_dir=tmp_path)
    result = asyncio.run(
        exporter.export(FakeClient([message(1, "Первая мысль"), message(2, "Вторая мысль")]), peer="@test_chat")
    )
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("private", encoding="utf-8")

    analysis = read_export_for_analysis(result.path, output_dir=tmp_path, max_chars=100)

    assert analysis.text.startswith("[2030-01-01")
    assert "Вторая мысль" in analysis.text
    assert analysis.truncated is False
    with pytest.raises(TelegramExportError, match="папке экспортов"):
        read_export_for_analysis(outside, output_dir=tmp_path)


def test_skill_returns_the_export_as_a_telegram_document() -> None:
    root = Path(__file__).resolve().parents[1]
    skill = (root / "hermes" / "skills" / "telegram-chat-export" / "SKILL.md").read_text(encoding="utf-8")

    assert "MEDIA:<path>" in skill
    assert "[[as_document]]" in skill
    assert "telegram_text_export_excerpt" in skill
    assert "queue_analysis_confirmed" in skill
