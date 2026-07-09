from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass(frozen=True)
class MonitorJob:
    id: int
    user_id: int
    chat_id: int
    source_type: str
    source_config: dict[str, str]
    condition_text: str
    enabled: bool = True
    last_state_hash: str | None = None
    last_payload: dict | None = None
    last_checked_at: datetime | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class MonitorRun:
    id: int
    monitor_job_id: int
    status: str
    triggered: bool = False
    error: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class MonitorDecision:
    triggered: bool
    message: str | None = None
