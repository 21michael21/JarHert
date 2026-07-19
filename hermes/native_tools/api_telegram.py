"""Telegram export/download methods of NativeToolsAPI, split out of the god object."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from .api_payload import document_attachment
from .telegram_text_export import read_document_excerpt, read_export_for_analysis

if TYPE_CHECKING:
    from .mcp_api import Confirmer, NativeToolsAPI


class TelegramExportMixin:
    if TYPE_CHECKING:
        exporter: "NativeToolsAPI.exporter"
        file_downloader: "NativeToolsAPI.file_downloader"
        _capabilities: "NativeToolsAPI._capabilities"
        coding_job_enqueue: "NativeToolsAPI.coding_job_enqueue"

    def telegram_text_export(
        self,
        *,
        peer: str,
        output_format: str = "txt",
        limit: int = 5000,
        confirmed: bool = False,
    ) -> dict[str, Any]:
        if not confirmed:
            raise ValueError("Экспорт требует одно явное подтверждение пользователя.")
        result = self.exporter(peer=peer, output_format=output_format, limit=limit)
        return {
            "path": str(result.path),
            "peer": result.peer,
            "title": result.title,
            "message_count": result.message_count,
            "output_format": result.output_format,
            "truncated": result.truncated,
            "expires_at": result.expires_at.isoformat(),
            "attachment": document_attachment(result.path),
        }

    async def telegram_text_export_confirmed(
        self,
        *,
        peer: str,
        output_format: str = "txt",
        limit: int = 5000,
        confirmer: Confirmer,
    ) -> dict[str, Any]:
        self._capabilities().require("telegram.export")
        preview = f"Экспортировать текст Telegram peer {peer}: до {limit} сообщений, формат {output_format}."
        if not await confirmer(preview):
            return {"status": "cancelled"}
        return await asyncio.to_thread(
            self.telegram_text_export,
            peer=peer,
            output_format=output_format,
            limit=limit,
            confirmed=True,
        )

    def telegram_file_download(
        self,
        *,
        peer: str,
        file_limit: int = 5,
        scan_limit: int = 500,
        message_ids: list[int] | None = None,
        confirmed: bool = False,
    ) -> dict[str, Any]:
        if not confirmed:
            raise ValueError("Загрузка файлов требует одно явное подтверждение пользователя.")
        result = self.file_downloader(
            peer=peer,
            file_limit=file_limit,
            scan_limit=scan_limit,
            message_ids=message_ids,
        )
        return {
            "status": "ok",
            "peer": result.peer,
            "title": result.title,
            "items": [
                {
                    "message_id": item.message_id,
                    "name": item.name,
                    "size_bytes": item.size_bytes,
                    "mime_type": item.mime_type,
                    "attachment": document_attachment(item.path),
                }
                for item in result.items
            ],
            "skipped_oversized": result.skipped_oversized,
            "expires_at": result.expires_at.isoformat(),
        }

    async def telegram_file_download_confirmed(
        self,
        *,
        peer: str,
        file_limit: int = 5,
        scan_limit: int = 500,
        message_ids: list[int] | None = None,
        confirmer: Confirmer,
    ) -> dict[str, Any]:
        self._capabilities().require("telegram.export")
        preview = (
            f"Скачать из Telegram peer {peer}: до {file_limit} файлов, "
            f"просмотреть до {scan_limit} сообщений, максимум 20 МБ на файл."
        )
        if not await confirmer(preview):
            return {"status": "cancelled"}
        return await asyncio.to_thread(
            self.telegram_file_download,
            peer=peer,
            file_limit=file_limit,
            scan_limit=scan_limit,
            message_ids=message_ids,
            confirmed=True,
        )

    def telegram_text_export_excerpt(self, *, path: str, max_chars: int = 120_000) -> dict[str, Any]:
        self._capabilities().require("telegram.export.read")
        result = read_export_for_analysis(path, max_chars=max_chars)
        return {
            "path": str(result.path),
            "text": result.text,
            "source_chars": result.source_chars,
            "truncated": result.truncated,
        }

    def telegram_file_read_excerpt(self, *, path: str, max_chars: int = 120_000) -> dict[str, Any]:
        self._capabilities().require("telegram.export.read")
        result = read_document_excerpt(path, max_chars=max_chars)
        return {
            "path": str(result.path),
            "text": result.text,
            "source_chars": result.source_chars,
            "truncated": result.truncated,
        }

    def telegram_text_export_queue_analysis(
        self,
        *,
        path: str,
        question: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        self._capabilities().require("telegram.export.read")
        result = read_export_for_analysis(path)
        return self.coding_job_enqueue(
            mode="research",
            prompt=question,
            idempotency_key=idempotency_key,
            source_text=result.text,
            source_label=result.path.name,
        )
