from __future__ import annotations

from datetime import datetime, timezone


_SENSITIVE_KEYS = {
    "text",
    "prompt",
    "message",
    "goal",
    "token",
    "secret",
    "authorization",
    "content",
    "error",
    "last_error",
    "result_text",
}


def queue_lag_ms(created_at: datetime | None, claimed_at: datetime | None) -> int | None:
    return _elapsed_ms(created_at, claimed_at)


def delivery_latency_ms(created_at: datetime | None, delivered_at: datetime | None) -> int | None:
    return _elapsed_ms(created_at, delivered_at)


def sanitize_observability_meta(meta: dict | None) -> dict:
    clean: dict[str, object] = {}
    for key, value in (meta or {}).items():
        normalized_key = str(key)
        if normalized_key.lower() in _SENSITIVE_KEYS or _looks_sensitive(normalized_key, value):
            continue
        clean[normalized_key] = _sanitize_value(value)
    return clean


def _elapsed_ms(start: datetime | None, end: datetime | None) -> int | None:
    if start is None or end is None:
        return None
    start = _as_utc(start)
    end = _as_utc(end)
    return max(0, round((end - start).total_seconds() * 1_000))


def _as_utc(value: datetime) -> datetime:
    # SQLite may return naive values even for timezone-aware model columns.
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _looks_sensitive(key: str, value: object) -> bool:
    lowered = key.lower()
    if any(marker in lowered for marker in ("token", "secret", "password", "key")):
        return True
    return isinstance(value, str) and any(marker in value.lower() for marker in ("sk-", "bearer ", "api_key"))


def _sanitize_value(value: object) -> object:
    if isinstance(value, dict):
        return sanitize_observability_meta(value)
    if isinstance(value, list):
        return [_sanitize_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_sanitize_value(item) for item in value)
    return value
