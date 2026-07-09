from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol


logger = logging.getLogger(__name__)


class DocsSync(Protocol):
    def append(
        self,
        *,
        kind: str,
        user_id: int,
        text: str,
        created_at: datetime | None = None,
        record_id: str | None = None,
    ) -> bool:
        ...


@dataclass(frozen=True)
class NullDocsSync:
    def append(
        self,
        *,
        kind: str,
        user_id: int,
        text: str,
        created_at: datetime | None = None,
        record_id: str | None = None,
    ) -> bool:
        return False


@dataclass(frozen=True)
class GoogleDocsWebhookSync:
    url: str
    token: str = ""
    timeout_seconds: float = 5.0

    def append(
        self,
        *,
        kind: str,
        user_id: int,
        text: str,
        created_at: datetime | None = None,
        record_id: str | None = None,
    ) -> bool:
        if not self.url:
            return False
        payload = {
            "kind": kind,
            "user_id": user_id,
            "text": text,
            "created_at": (created_at or datetime.now(timezone.utc)).isoformat(),
            "record_id": record_id or "",
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "telegram-ai-brooch/0.1",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(self.url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                return 200 <= response.status < 300
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            logger.warning("Google Docs webhook sync failed: %s", exc)
            return False
