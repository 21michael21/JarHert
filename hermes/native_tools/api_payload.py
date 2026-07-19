"""Shared payload helpers for NativeToolsAPI facade and its mixins."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def value_payload(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, tuple):
        return [value_payload(item) for item in value]
    if isinstance(value, list):
        return [value_payload(item) for item in value]
    if isinstance(value, dict):
        return {key: value_payload(item) for key, item in value.items()}
    if hasattr(value, "__dataclass_fields__"):
        return {name: value_payload(getattr(value, name)) for name in value.__dataclass_fields__}
    return value


def document_attachment(path: Path) -> dict[str, str]:
    value = str(path)
    return {
        "path": value,
        "directive": f"[[as_document]]\nMEDIA:{value}",
    }


def short_text(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    clean = " ".join(value.split())
    return clean if len(clean) <= limit else clean[: limit - 1].rstrip() + "…"


def coding_job_summary(job: Any) -> dict[str, Any]:
    """Keep routine status checks small; the full report is fetched explicitly."""
    return {
        "id": job.id,
        "mode": job.mode,
        "prompt": short_text(job.prompt, 180),
        "repository_url": job.repository_url,
        "source_label": job.source_label,
        "status": job.status,
        "result_summary": short_text(job.result_text, 160),
        "last_error": short_text(job.last_error, 160),
        "delivery_status": job.delivery_status,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
    }
