from __future__ import annotations

import json
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol
from urllib.parse import urlparse

from assistant.monitors.github_releases import fetch_latest_github_release
from assistant.monitors.models import MonitorJob


JsonFetcher = Callable[[str], dict[str, Any]]
TextFetcher = Callable[[str], str]


class MonitorSource(Protocol):
    source_type: str

    def fetch(self, job: MonitorJob) -> dict[str, Any]:
        ...


@dataclass(frozen=True)
class GitHubReleasesSource:
    source_type: str = "github_releases"

    def fetch(self, job: MonitorJob) -> dict[str, Any]:
        owner = str(job.source_config.get("owner") or "")
        repo = str(job.source_config.get("repo") or "")
        return fetch_latest_github_release(owner, repo)


@dataclass(frozen=True)
class RssSource:
    http_text_fetcher: TextFetcher
    source_type: str = "rss"

    def fetch(self, job: MonitorJob) -> dict[str, Any]:
        url = _require_https_url(job.source_config.get("url", ""))
        raw = self.http_text_fetcher(url)
        root = ET.fromstring(raw)
        channel = root.find("channel")
        items_parent = channel if channel is not None else root
        items: list[dict[str, str]] = []
        for item in items_parent.findall(".//item")[:10]:
            items.append(
                {
                    "title": _xml_text(item, "title"),
                    "link": _xml_text(item, "link"),
                    "published": _xml_text(item, "pubDate"),
                }
            )
        return {
            "url": url,
            "feed_title": _xml_text(channel, "title") if channel is not None else "",
            "items": items,
        }


@dataclass(frozen=True)
class AllowedHttpApiSource:
    http_json_fetcher: JsonFetcher
    source_type: str = "http_api"

    def fetch(self, job: MonitorJob) -> dict[str, Any]:
        url = _require_https_url(job.source_config.get("url", ""))
        host = urlparse(url).hostname or ""
        allowed_hosts = _allowed_hosts(job.source_config)
        if host not in allowed_hosts:
            raise ValueError("HTTP API host is not in monitor allowlist")
        payload = self.http_json_fetcher(url)
        if not isinstance(payload, dict):
            raise ValueError("HTTP API monitor must return a JSON object")
        return {"url": url, "payload": payload}


@dataclass(frozen=True)
class TelegramTrendsSource:
    message_store: object | None = None
    source_type: str = "telegram_trends"

    def fetch(self, job: MonitorJob) -> dict[str, Any]:
        if self.message_store is None:
            messages = job.source_config.get("messages") or []
            return {"messages": messages[:50] if isinstance(messages, list) else [], "count": len(messages) if isinstance(messages, list) else 0}
        lookback_hours = int(job.source_config.get("lookback_hours") or 6)
        limit = int(job.source_config.get("limit") or 200)
        since = datetime.now(timezone.utc) - timedelta(hours=max(1, lookback_hours))
        rows = self.message_store.list_unprocessed(since=since, limit=limit)
        messages = [
            {
                "chat_title": row.chat_title or str(row.chat_id),
                "sender_name": row.sender_name or str(row.sender_id or ""),
                "text": " ".join(row.text.split())[:700],
                "timestamp": row.timestamp.isoformat(),
            }
            for row in rows
            if row.text.strip()
        ]
        return {"count": len(messages), "messages": messages}


class MonitorSourceRegistry:
    def __init__(
        self,
        *,
        http_json_fetcher: JsonFetcher | None = None,
        http_text_fetcher: TextFetcher | None = None,
        message_store: object | None = None,
    ) -> None:
        json_fetcher = http_json_fetcher or _fetch_json
        text_fetcher = http_text_fetcher or _fetch_text
        self._sources: dict[str, MonitorSource] = {
            "github_releases": GitHubReleasesSource(),
            "rss": RssSource(text_fetcher),
            "http_api": AllowedHttpApiSource(json_fetcher),
            "telegram_trends": TelegramTrendsSource(message_store),
        }

    def fetch(self, job: MonitorJob) -> dict[str, Any]:
        try:
            source = self._sources[job.source_type]
        except KeyError as exc:
            raise ValueError(f"Unsupported monitor source_type: {job.source_type}") from exc
        return source.fetch(job)


def _fetch_json(url: str) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=20) as response:  # noqa: S310 - URL is allowlisted by caller.
        data = json.loads(response.read().decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Expected JSON object")
    return data


def _fetch_text(url: str) -> str:
    with urllib.request.urlopen(url, timeout=20) as response:  # noqa: S310 - URL is a configured monitor source.
        return response.read().decode("utf-8")


def _require_https_url(value: str) -> str:
    url = str(value or "").strip()
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("Monitor source URL must be https and include a host")
    return url


def _allowed_hosts(config: dict[str, Any]) -> set[str]:
    raw = config.get("allowed_hosts") or config.get("allowed_host") or []
    if isinstance(raw, str):
        values = [item.strip() for item in raw.split(",")]
    elif isinstance(raw, list):
        values = [str(item).strip() for item in raw]
    else:
        values = []
    return {value for value in values if value}


def _xml_text(node: ET.Element | None, name: str) -> str:
    if node is None:
        return ""
    child = node.find(name)
    return (child.text or "").strip() if child is not None else ""
