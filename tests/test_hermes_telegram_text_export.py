from __future__ import annotations

import asyncio
import json
import os
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from hermes.native_tools.telegram_text_export import (
    ExportMessage,
    FileDownloadResult,
    TelegramFileDownloader,
    TelegramFileMessage,
    TelegramExportError,
    TelethonTextClient,
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


class FakeFileClient(FakeClient):
    def __init__(self, files, *, accessible=True) -> None:
        super().__init__([], accessible=accessible)
        self.files = files

    async def iter_file_messages(self, entity, *, scan_limit):
        for item in self.files[:scan_limit]:
            yield item

    async def download_file(self, file_message, destination):
        destination.write_bytes(file_message.source)
        return len(file_message.source)


class RawTelethonClient:
    def __init__(self, messages) -> None:
        self.messages = messages
        self.limits: list[int] = []

    async def iter_messages(self, _entity, *, limit):
        self.limits.append(limit)
        for item in self.messages[:limit]:
            yield item


def message(number: int, text: str) -> ExportMessage:
    return ExportMessage(
        message_id=number,
        date=datetime(2030, 1, number, 12, tzinfo=timezone.utc),
        sender_id=100 + number,
        sender_name=f"User {number}",
        text=text,
        reply_to_message_id=None,
    )


def file_message(number: int, name: str, content: bytes) -> TelegramFileMessage:
    return TelegramFileMessage(
        message_id=number,
        date=datetime(2030, 1, number, 12, tzinfo=timezone.utc),
        name=name,
        size_bytes=len(content),
        mime_type="application/octet-stream",
        source=content,
    )


async def _collect(iterator):
    return [item async for item in iterator]


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


def test_export_does_not_create_an_empty_document(tmp_path) -> None:
    exporter = TelegramTextExporter(output_dir=tmp_path)

    with pytest.raises(TelegramExportError, match="нет текстовых сообщений"):
        asyncio.run(exporter.export(FakeClient([]), peer="@test_chat"))

    assert not list(tmp_path.glob("*.txt"))
    assert not list(tmp_path.glob("*.part"))


def test_exported_files_and_directory_are_owner_readable_only(tmp_path) -> None:
    exporter = TelegramTextExporter(output_dir=tmp_path / "exports")

    result = asyncio.run(
        exporter.export(FakeClient([message(1, "Приватное")]), peer="@test_chat", output_format="txt")
    )

    assert stat.S_IMODE(result.path.stat().st_mode) == 0o600
    assert stat.S_IMODE(result.path.parent.stat().st_mode) == 0o700


def test_downloaded_files_are_owner_readable_only(tmp_path) -> None:
    downloader = TelegramFileDownloader(output_dir=tmp_path / "exports")

    result = asyncio.run(
        downloader.download(
            FakeFileClient([file_message(10, "plan.txt", b"read me")]),
            peer="@test_chat",
            file_limit=5,
            scan_limit=20,
        )
    )

    assert stat.S_IMODE(result.items[0].path.stat().st_mode) == 0o600


def test_telethon_reader_collects_requested_texts_past_recent_media() -> None:
    def raw(number: int, text: str) -> SimpleNamespace:
        return SimpleNamespace(
            id=number,
            date=datetime(2030, 1, 1, 12, tzinfo=timezone.utc),
            message=text,
            sender=SimpleNamespace(first_name="Sender", last_name=None, title=None, username=None),
            sender_id=100 + number,
            reply_to=None,
        )

    client = TelethonTextClient(api_id=1, api_hash="hash", session_path="unused")
    raw_client = RawTelethonClient([raw(1, ""), raw(2, ""), raw(3, "Первое"), raw(4, "Второе")])
    client.client = raw_client

    items = asyncio.run(_collect(client.iter_text_messages(object(), limit=2)))

    assert [item.text for item in items] == ["Второе", "Первое"]
    assert raw_client.limits == [100]


def test_file_download_keeps_small_documents_temporarily_and_skips_large_ones(tmp_path) -> None:
    downloader = TelegramFileDownloader(output_dir=tmp_path)
    small = file_message(10, "plan.txt", b"read me")
    oversized = file_message(11, "large.bin", b"X" * (20 * 1024 * 1024 + 1))

    result = asyncio.run(
        downloader.download(
            FakeFileClient([small, oversized]),
            peer="@test_chat",
            file_limit=5,
            scan_limit=20,
        )
    )

    assert len(result.items) == 1
    assert result.items[0].path.read_bytes() == b"read me"
    assert result.items[0].path.name.startswith("telegram_file_10_")
    assert result.skipped_oversized == 1
    assert result.expires_at > datetime.now(timezone.utc) + timedelta(hours=47)
    assert not list(tmp_path.glob("*.part"))


def test_limit_is_bounded(tmp_path) -> None:
    exporter = TelegramTextExporter(output_dir=tmp_path)

    with pytest.raises(TelegramExportError, match="50000"):
        asyncio.run(exporter.export(FakeClient([]), peer="@test_chat", output_format="txt", limit=50_001))


def test_cleanup_removes_only_expired_telegram_exports(tmp_path) -> None:
    now = datetime(2030, 1, 3, 12, tzinfo=timezone.utc)
    old_export = tmp_path / "chat_20300101_110000.txt"
    old_download = tmp_path / "telegram_file_8_20300101_110000_notes.pdf"
    fresh_export = tmp_path / "chat_20300103_110000.jsonl"
    unrelated = tmp_path / "keep-me.txt"
    linked_target = tmp_path / "outside.txt"
    linked_export = tmp_path / "linked_20300101_110000.txt"
    for path in (old_export, old_download, fresh_export, unrelated):
        path.write_text("text", encoding="utf-8")
    linked_target.write_text("must remain", encoding="utf-8")
    linked_export.symlink_to(linked_target)
    old_timestamp = (now - timedelta(hours=49)).timestamp()
    fresh_timestamp = (now - timedelta(hours=1)).timestamp()
    os.utime(old_export, (old_timestamp, old_timestamp))
    os.utime(fresh_export, (fresh_timestamp, fresh_timestamp))

    removed = cleanup_expired_exports(tmp_path, now=now)

    assert removed == 2
    assert not old_export.exists()
    assert not old_download.exists()
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
    assert "Не читай экспорт автоматически" in skill
    assert "не отказывайся читать" in skill
    assert "последние N" in skill
    assert "весь чат" in skill
