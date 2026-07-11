from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass(frozen=True)
class CodingJob:
    id: int
    user_id: int
    mode: str
    prompt: str
    status: str
    repository_url: str | None = None
    source_urls: list[str] = field(default_factory=list)
    idempotency_key: str | None = None
    worker_id: str | None = None
    lease_until: datetime | None = None
    heartbeat_at: datetime | None = None
    result_text: str | None = None
    last_error: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
