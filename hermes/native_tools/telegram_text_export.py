from __future__ import annotations

import asyncio
import json
import os
import re
import stat
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Protocol


DEFAULT_EXPORT_RETENTION_HOURS = 48
DEFAULT_EXPORT_ANALYSIS_CHARS = 120_000
MAX_DOWNLOAD_FILE_BYTES = 20 * 1024 * 1024
_EXPORT_FILE_NAME = re.compile(r"^[A-Za-z0-9А-Яа-яЁё._-]+_\d{8}_\d{6}\.(?:txt|jsonl)(?:\.part)?$")
_DOWNLOAD_FILE_NAME = re.compile(
    r"^telegram_file_\d+_\d{8}_\d{6}_[A-Za-z0-9А-Яа-яЁё._-]{1,100}(?:\.part)?$"
)


class TelegramExportError(RuntimeError):
    pass


@dataclass(frozen=True)
class ExportMessage:
    message_id: int
    date: datetime
    sender_id: int | None
    sender_name: str | None
    text: str
    reply_to_message_id: int | None


@dataclass(frozen=True)
class ExportResult:
    path: Path
    peer: str
    title: str
    message_count: int
    output_format: str
    truncated: bool
    expires_at: datetime


@dataclass(frozen=True)
class ExportAnalysis:
    """A bounded, owner-requested view of one temporary text export."""

    path: Path
    text: str
    source_chars: int
    truncated: bool


@dataclass(frozen=True)
class TelegramFileMessage:
    message_id: int
    date: datetime
    name: str
    size_bytes: int
    mime_type: str | None
    source: Any = field(default=None, repr=False, compare=False)


@dataclass(frozen=True)
class FileDownloadItem:
    message_id: int
    path: Path
    name: str
    size_bytes: int
    mime_type: str | None


@dataclass(frozen=True)
class FileDownloadResult:
    peer: str
    title: str
    items: tuple[FileDownloadItem, ...]
    skipped_oversized: int
    expires_at: datetime


class TextHistoryClient(Protocol):
    async def resolve_peer(self, peer: str | int) -> Any: ...
    async def is_accessible_dialog(self, entity: Any) -> bool: ...
    def iter_text_messages(self, entity: Any, *, limit: int) -> AsyncIterator[ExportMessage]: ...


class FileHistoryClient(Protocol):
    async def resolve_peer(self, peer: str | int) -> Any: ...
    async def is_accessible_dialog(self, entity: Any) -> bool: ...
    def iter_file_messages(self, entity: Any, *, scan_limit: int) -> AsyncIterator[TelegramFileMessage]: ...
    async def download_file(self, file_message: TelegramFileMessage, destination: Path) -> int: ...


class TelegramTextExporter:
    def __init__(
        self,
        *,
        output_dir: str | Path,
        max_output_bytes: int = 20 * 1024 * 1024,
        retention_hours: int = DEFAULT_EXPORT_RETENTION_HOURS,
    ) -> None:
        self.output_dir = Path(output_dir).expanduser()
        self.max_output_bytes = max(1024, min(int(max_output_bytes), 20 * 1024 * 1024))
        self.retention_hours = _retention_hours(retention_hours)

    async def export(
        self,
        client: TextHistoryClient,
        *,
        peer: str,
        output_format: str = "txt",
        limit: int = 5000,
    ) -> ExportResult:
        cleanup_expired_exports(self.output_dir, retention_hours=self.retention_hours)
        normalized_peer = normalize_peer(peer)
        if not 1 <= limit <= 50_000:
            raise TelegramExportError("Export limit должен быть от 1 до 50000 сообщений.")
        file_format = output_format.strip().lower()
        if file_format not in {"txt", "jsonl"}:
            raise TelegramExportError("Export format должен быть txt или jsonl.")

        entity = await client.resolve_peer(normalized_peer)
        if not await client.is_accessible_dialog(entity):
            raise TelegramExportError("Этот peer нет среди диалогов авторизованного Telegram-аккаунта.")
        title = _entity_title(entity, str(peer))
        self.output_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        destination = self.output_dir / f"{_safe_filename(title)}_{stamp}.{file_format}"
        partial = destination.with_suffix(destination.suffix + ".part")
        count = 0
        size = 0
        truncated = False
        try:
            with partial.open("wb") as handle:
                async for message in client.iter_text_messages(entity, limit=limit):
                    block = _serialize_message(message, file_format)
                    encoded = block.encode("utf-8")
                    if size + len(encoded) > self.max_output_bytes:
                        truncated = True
                        break
                    handle.write(encoded)
                    size += len(encoded)
                    count += 1
            partial.replace(destination)
        except Exception:
            partial.unlink(missing_ok=True)
            raise
        return ExportResult(
            path=destination,
            peer=str(peer),
            title=title,
            message_count=count,
            output_format=file_format,
            truncated=truncated,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=self.retention_hours),
        )


class TelegramFileDownloader:
    """Download only owner-requested Telegram documents into the short-lived export area."""

    def __init__(
        self,
        *,
        output_dir: str | Path,
        max_file_bytes: int = MAX_DOWNLOAD_FILE_BYTES,
        retention_hours: int = DEFAULT_EXPORT_RETENTION_HOURS,
    ) -> None:
        self.output_dir = Path(output_dir).expanduser()
        self.max_file_bytes = max(1024, min(int(max_file_bytes), MAX_DOWNLOAD_FILE_BYTES))
        self.retention_hours = _retention_hours(retention_hours)

    async def download(
        self,
        client: FileHistoryClient,
        *,
        peer: str,
        file_limit: int = 5,
        scan_limit: int = 500,
        message_ids: list[int] | None = None,
    ) -> FileDownloadResult:
        cleanup_expired_exports(self.output_dir, retention_hours=self.retention_hours)
        normalized_peer = normalize_peer(peer)
        clean_file_limit = _bounded_positive(file_limit, minimum=1, maximum=20, label="File limit")
        clean_scan_limit = _bounded_positive(scan_limit, minimum=1, maximum=50_000, label="Scan limit")
        requested_ids = {int(item) for item in (message_ids or [])}
        if len(requested_ids) > 20 or any(item <= 0 for item in requested_ids):
            raise TelegramExportError("Message IDs должны содержать от 1 до 20 положительных значений.")
        entity = await client.resolve_peer(normalized_peer)
        if not await client.is_accessible_dialog(entity):
            raise TelegramExportError("Этот peer нет среди диалогов авторизованного Telegram-аккаунта.")
        title = _entity_title(entity, str(peer))
        self.output_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        downloaded: list[FileDownloadItem] = []
        skipped_oversized = 0
        async for item in client.iter_file_messages(entity, scan_limit=clean_scan_limit):
            if requested_ids and item.message_id not in requested_ids:
                continue
            if item.size_bytes > self.max_file_bytes:
                skipped_oversized += 1
                continue
            destination = self.output_dir / _download_name(item, stamp)
            partial = destination.with_name(destination.name + ".part")
            try:
                written = await client.download_file(item, partial)
                if written <= 0 or written > self.max_file_bytes:
                    raise TelegramExportError("Telegram вернул файл с недопустимым размером.")
                partial.replace(destination)
            except Exception:
                partial.unlink(missing_ok=True)
                raise
            downloaded.append(
                FileDownloadItem(
                    message_id=item.message_id,
                    path=destination,
                    name=destination.name,
                    size_bytes=written,
                    mime_type=item.mime_type,
                )
            )
            if len(downloaded) >= clean_file_limit:
                break
        return FileDownloadResult(
            peer=str(peer),
            title=title,
            items=tuple(downloaded),
            skipped_oversized=skipped_oversized,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=self.retention_hours),
        )


class TelethonTextClient:
    def __init__(self, *, api_id: int, api_hash: str, session_path: str | Path) -> None:
        self.api_id = api_id
        self.api_hash = api_hash
        self.session_path = str(Path(session_path).expanduser())
        self.client: Any = None

    async def connect(self) -> None:
        try:
            from telethon import TelegramClient
        except ModuleNotFoundError as error:
            raise TelegramExportError("Telethon не установлен.") from error
        self.client = TelegramClient(self.session_path, self.api_id, self.api_hash)
        await self.client.connect()
        if not await self.client.is_user_authorized():
            await self.client.disconnect()
            raise TelegramExportError(
                "MTProto session не авторизована. Запусти интерактивный setup локально."
            )

    async def close(self) -> None:
        if self.client is not None:
            await self.client.disconnect()

    async def resolve_peer(self, peer: str | int) -> Any:
        return await self.client.get_entity(peer)

    async def is_accessible_dialog(self, entity: Any) -> bool:
        entity_id = getattr(entity, "id", None)
        async for dialog in self.client.iter_dialogs():
            if getattr(dialog.entity, "id", None) == entity_id:
                return True
        return False

    async def iter_text_messages(self, entity: Any, *, limit: int) -> AsyncIterator[ExportMessage]:
        async for message in self.client.iter_messages(entity, limit=limit, reverse=True):
            text = str(getattr(message, "message", "") or "")
            if not text.strip():
                continue
            sender = getattr(message, "sender", None)
            if sender is None:
                try:
                    sender = await message.get_sender()
                except Exception:
                    sender = None
            yield ExportMessage(
                message_id=int(message.id),
                date=_aware(message.date),
                sender_id=_optional_int(getattr(message, "sender_id", None)),
                sender_name=_sender_name(sender),
                text=text,
                reply_to_message_id=_reply_id(message),
            )

    async def iter_file_messages(self, entity: Any, *, scan_limit: int) -> AsyncIterator[TelegramFileMessage]:
        async for message in self.client.iter_messages(entity, limit=scan_limit):
            document = getattr(message, "document", None)
            file = getattr(message, "file", None)
            size = _optional_int(getattr(file, "size", None))
            if document is None or size is None or size <= 0:
                continue
            name = str(getattr(file, "name", "") or f"document_{message.id}")
            yield TelegramFileMessage(
                message_id=int(message.id),
                date=_aware(message.date),
                name=name,
                size_bytes=size,
                mime_type=str(getattr(file, "mime_type", "") or "") or None,
                source=message,
            )

    async def download_file(self, file_message: TelegramFileMessage, destination: Path) -> int:
        downloaded = await self.client.download_media(file_message.source, file=str(destination))
        actual = Path(str(downloaded or destination))
        if not actual.is_file():
            raise TelegramExportError("Telegram не сохранил запрошенный файл.")
        if actual != destination:
            actual.replace(destination)
        return destination.stat().st_size


def run_telegram_export(*, peer: str, output_format: str = "txt", limit: int = 5000) -> ExportResult:
    api_id, api_hash, session_path, output_dir = telegram_export_settings()

    async def run() -> ExportResult:
        client = TelethonTextClient(api_id=api_id, api_hash=api_hash, session_path=session_path)
        await client.connect()
        try:
            return await TelegramTextExporter(
                output_dir=output_dir,
                retention_hours=telegram_export_retention_hours(),
            ).export(
                client, peer=peer, output_format=output_format, limit=limit
            )
        finally:
            await client.close()

    return asyncio.run(run())


def run_telegram_file_download(
    *,
    peer: str,
    file_limit: int = 5,
    scan_limit: int = 500,
    message_ids: list[int] | None = None,
) -> FileDownloadResult:
    api_id, api_hash, session_path, output_dir = telegram_export_settings()

    async def run() -> FileDownloadResult:
        client = TelethonTextClient(api_id=api_id, api_hash=api_hash, session_path=session_path)
        await client.connect()
        try:
            return await TelegramFileDownloader(
                output_dir=output_dir,
                retention_hours=telegram_export_retention_hours(),
            ).download(
                client,
                peer=peer,
                file_limit=file_limit,
                scan_limit=scan_limit,
                message_ids=message_ids,
            )
        finally:
            await client.close()

    return asyncio.run(run())


def telegram_session_status() -> dict[str, bool]:
    api_id, api_hash, session_path, _output_dir = telegram_export_settings()

    async def check() -> bool:
        try:
            from telethon import TelegramClient
        except ModuleNotFoundError as error:
            raise TelegramExportError("Telethon не установлен.") from error
        client = TelegramClient(str(session_path), api_id, api_hash)
        await client.connect()
        try:
            return bool(await client.is_user_authorized())
        finally:
            await client.disconnect()

    return {"configured": True, "authorized": asyncio.run(check())}


def telegram_export_settings() -> tuple[int, str, Path, Path]:
    raw_id = os.getenv("TELEGRAM_API_ID", "").strip()
    api_hash = os.getenv("TELEGRAM_API_HASH", "").strip()
    if not raw_id or not api_hash:
        raise TelegramExportError("TELEGRAM_API_ID и TELEGRAM_API_HASH не настроены.")
    try:
        api_id = int(raw_id)
    except ValueError as error:
        raise TelegramExportError("TELEGRAM_API_ID должен быть целым числом.") from error
    home = Path(os.getenv("HERMES_HOME", "~/.hermes")).expanduser()
    session_value = os.getenv("TELEGRAM_USER_SESSION", "").strip() or str(home / "data" / "telegram-user.session")
    session = Path(session_value)
    output = telegram_export_output_directory()
    return api_id, api_hash, session.expanduser(), output.expanduser()


def telegram_export_output_directory() -> Path:
    home = Path(os.getenv("HERMES_HOME", "~/.hermes")).expanduser()
    output_value = os.getenv("TELEGRAM_EXPORT_DIR", "").strip() or str(home / "exports" / "telegram")
    return Path(output_value).expanduser()


def telegram_export_retention_hours() -> int:
    return _retention_hours(os.getenv("TELEGRAM_EXPORT_RETENTION_HOURS", str(DEFAULT_EXPORT_RETENTION_HOURS)))


def read_export_for_analysis(
    path: str | Path,
    *,
    output_dir: str | Path | None = None,
    max_chars: int = DEFAULT_EXPORT_ANALYSIS_CHARS,
) -> ExportAnalysis:
    """Read a bounded sample from one generated export after an explicit owner request."""
    directory = Path(output_dir or telegram_export_output_directory()).expanduser().resolve()
    candidate = Path(path).expanduser().resolve()
    if not candidate.is_relative_to(directory):
        raise TelegramExportError("Для анализа доступен только файл в папке экспортов Telegram.")
    if not _EXPORT_FILE_NAME.fullmatch(candidate.name) or not candidate.is_file():
        raise TelegramExportError("Файл не является действующим текстовым экспортом Telegram.")
    mode = candidate.stat().st_mode
    if not stat.S_ISREG(mode):
        raise TelegramExportError("Экспорт должен быть обычным файлом.")
    try:
        source = candidate.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        raise TelegramExportError("Экспорт должен быть UTF-8 текстом.") from error
    cap = max(1_000, min(int(max_chars), DEFAULT_EXPORT_ANALYSIS_CHARS))
    text, truncated = _sample_text(source, cap)
    return ExportAnalysis(path=candidate, text=text, source_chars=len(source), truncated=truncated)


def cleanup_expired_exports(
    output_dir: str | Path,
    *,
    retention_hours: int = DEFAULT_EXPORT_RETENTION_HOURS,
    now: datetime | None = None,
) -> int:
    """Delete only old regular TXT/JSONL files produced by this exporter."""
    directory = Path(output_dir).expanduser()
    if not directory.is_dir():
        return 0
    cutoff = (now or datetime.now(timezone.utc)).timestamp() - _retention_hours(retention_hours) * 3600
    removed = 0
    for entry in directory.iterdir():
        if not _managed_export_name(entry.name):
            continue
        try:
            mode = entry.lstat().st_mode
            if not stat.S_ISREG(mode) or entry.stat().st_mtime > cutoff:
                continue
            entry.unlink()
            removed += 1
        except FileNotFoundError:
            continue
    return removed


def _sample_text(source: str, max_chars: int) -> tuple[str, bool]:
    if len(source) <= max_chars:
        return source, False
    # Keep the beginning, end and evenly spaced interior portions. This gives a
    # research worker broad coverage without turning a 20 MB export into one prompt.
    slices = 8
    chunk = max(1, (max_chars - (slices - 1) * 36) // slices)
    stride = max(1, (len(source) - chunk) // (slices - 1))
    parts = [source[index * stride:index * stride + chunk] for index in range(slices)]
    marker = "\n\n[... часть экспорта пропущена ...]\n\n"
    return marker.join(parts)[:max_chars], True


def normalize_peer(value: str) -> str | int:
    clean = value.strip()
    if re.fullmatch(r"-?\d{3,20}", clean):
        return int(clean)
    if re.fullmatch(r"@[A-Za-z0-9_]{5,32}", clean):
        return clean
    raise TelegramExportError("Peer должен быть numeric ID или @username.")


def _retention_hours(value: int | str) -> int:
    try:
        hours = int(value)
    except (TypeError, ValueError) as error:
        raise TelegramExportError("TELEGRAM_EXPORT_RETENTION_HOURS должен быть числом от 1 до 168.") from error
    if not 1 <= hours <= 168:
        raise TelegramExportError("TELEGRAM_EXPORT_RETENTION_HOURS должен быть от 1 до 168.")
    return hours


def _bounded_positive(value: int, *, minimum: int, maximum: int, label: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as error:
        raise TelegramExportError(f"{label} должен быть числом от {minimum} до {maximum}.") from error
    if not minimum <= parsed <= maximum:
        raise TelegramExportError(f"{label} должен быть от {minimum} до {maximum}.")
    return parsed


def _download_name(item: TelegramFileMessage, stamp: str) -> str:
    safe_name = _safe_filename(item.name)
    return f"telegram_file_{item.message_id}_{stamp}_{safe_name}"


def _managed_export_name(name: str) -> bool:
    return bool(_EXPORT_FILE_NAME.fullmatch(name) or _DOWNLOAD_FILE_NAME.fullmatch(name))


def _serialize_message(message: ExportMessage, output_format: str) -> str:
    if output_format == "jsonl":
        row = {
            "id": message.message_id,
            "date": _aware(message.date).isoformat(),
            "sender_id": message.sender_id,
            "sender_name": message.sender_name,
            "text": message.text,
            "reply_to_message_id": message.reply_to_message_id,
        }
        return json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
    sender = message.sender_name or (str(message.sender_id) if message.sender_id else "Unknown")
    return f"[{_aware(message.date).isoformat()}] {sender}\n{message.text}\n\n"


def _entity_title(entity: Any, fallback: str) -> str:
    if isinstance(entity, dict):
        return str(entity.get("title") or entity.get("username") or fallback)
    for attribute in ("title", "username", "first_name"):
        value = getattr(entity, attribute, None)
        if value:
            return str(value)
    return fallback


def _safe_filename(value: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9А-Яа-яЁё._-]+", "_", value).strip("._-")
    return clean[:80] or "telegram_chat"


def _sender_name(sender: Any) -> str | None:
    if sender is None:
        return None
    parts = [getattr(sender, "first_name", None), getattr(sender, "last_name", None)]
    name = " ".join(str(part).strip() for part in parts if str(part or "").strip())
    return name or str(getattr(sender, "title", None) or getattr(sender, "username", None) or "") or None


def _reply_id(message: Any) -> int | None:
    reply = getattr(message, "reply_to", None)
    return _optional_int(getattr(reply, "reply_to_msg_id", None)) if reply else None


def _optional_int(value: Any) -> int | None:
    return int(value) if value is not None else None


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
