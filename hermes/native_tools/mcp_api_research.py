from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from .mcp_api import Confirmer, _value_payload
from .monitors import Monitor
from .telegram_text_export import read_export_for_analysis


def _monitor_payload(monitor: Monitor) -> dict[str, Any]:
    return _value_payload(monitor)


def _document_attachment(path: Path) -> dict[str, str]:
    value = str(path)
    return {
        "path": value,
        "directive": f"[[as_document]]\nMEDIA:{value}",
    }


class ResearchMixin:
    def monitor_add_github_releases(
        self,
        *,
        name: str,
        owner: str,
        repo: str,
        condition: str,
    ) -> dict[str, Any]:
        self._capabilities().require("monitor.write")
        return _monitor_payload(
            self._monitors().add(
                name=name,
                source_type="github_releases",
                source_config={"owner": owner, "repo": repo},
                condition=condition,
            )
        )

    def monitor_add_source(
        self,
        *,
        name: str,
        source_type: str,
        url: str,
        allowed_hosts: list[str],
        condition: str,
        quiet_hours: str | None = None,
        timezone_name: str = "Europe/Moscow",
    ) -> dict[str, Any]:
        self._capabilities().require("monitor.write")
        source_config: dict[str, Any] = {
            "url": url,
            "allowed_hosts": allowed_hosts,
            "timezone": timezone_name,
        }
        if quiet_hours:
            source_config["quiet_hours"] = quiet_hours
        return _monitor_payload(
            self._monitors().add(
                name=name,
                source_type=source_type,
                source_config=source_config,
                condition=condition,
            )
        )

    def monitor_list(self) -> dict[str, Any]:
        self._capabilities().require("monitor.list")
        return {"items": [_monitor_payload(item) for item in self._monitors().list()]}

    def monitor_disable(self, *, monitor_id: int) -> dict[str, Any]:
        self._capabilities().require("monitor.write")
        return _monitor_payload(self._monitors().disable(monitor_id))

    def monitor_schedule_update(
        self,
        *,
        monitor_id: int,
        quiet_hours: str | None,
        timezone_name: str = "Europe/Moscow",
    ) -> dict[str, Any]:
        """Keep changed monitor events quiet and collect them for the existing digest."""
        self._capabilities().require("monitor.write")
        return _monitor_payload(
            self._monitors().update_schedule(
                monitor_id,
                quiet_hours=quiet_hours,
                timezone_name=timezone_name,
            )
        )

    def monitor_digest(self) -> dict[str, Any]:
        self._capabilities().require("monitor.list")
        return self._monitors().build_digest()

    def monitor_digest_mark_delivered(self, *, item_ids: list[int]) -> dict[str, int]:
        self._capabilities().require("monitor.write")
        self._monitors().mark_digest_delivered(item_ids)
        return {"delivered": len(set(int(item_id) for item_id in item_ids))}

    def knowledge_archive_url(self, *, url: str, project: str | None = None) -> dict[str, Any]:
        self._capabilities().require("knowledge.write")
        return self._knowledge().archive_url(url, project=project)

    def knowledge_archive_urls(self, *, urls: list[str], project: str | None = None) -> dict[str, Any]:
        self._capabilities().require("knowledge.write")
        if not urls or len(urls) > 20:
            raise ValueError("Для архива укажи от 1 до 20 явных URL.")
        items: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        archive = self._knowledge()
        for url in urls:
            try:
                items.append(archive.archive_url(str(url), project=project))
            except (OSError, ValueError) as error:
                errors.append({"url": str(url), "error": type(error).__name__})
        return {"items": items, "errors": errors}

    def knowledge_search(
        self,
        *,
        query: str,
        project: str | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        self._capabilities().require("knowledge.read")
        return {"items": self._knowledge().search(query, project=project, limit=limit)}

    def knowledge_source_excerpt(
        self,
        *,
        source_id: int,
        query: str | None = None,
    ) -> dict[str, Any]:
        self._capabilities().require("knowledge.read")
        return self._knowledge().source_excerpt(source_id, query=query)

    def knowledge_list_sources(
        self,
        *,
        project: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        self._capabilities().require("knowledge.read")
        return {"items": [_value_payload(item) for item in self._knowledge().list_sources(project=project, limit=limit)]}

    def github_public_repository(self, *, url: str) -> dict[str, Any]:
        self._capabilities().require("github.read")
        return self._github_public().inspect_repository(url)

    def telegram_text_export_excerpt(self, *, path: str, max_chars: int = 120_000) -> dict[str, Any]:
        self._capabilities().require("telegram.export.read")
        result = read_export_for_analysis(path, max_chars=max_chars)
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
            "attachment": _document_attachment(result.path),
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
                    "attachment": _document_attachment(item.path),
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
