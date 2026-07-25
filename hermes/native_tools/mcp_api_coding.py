from __future__ import annotations

import os
from typing import Any

from .mcp_api import _value_payload


def _coding_job_summary(job: Any) -> dict[str, Any]:
    """Keep routine status checks small; the full report is fetched explicitly."""
    return {
        "id": job.id,
        "mode": job.mode,
        "prompt": _short_text(job.prompt, 180),
        "repository_url": job.repository_url,
        "source_label": job.source_label,
        "status": job.status,
        "result_summary": _short_text(job.result_text, 160),
        "last_error": _short_text(job.last_error, 160),
        "delivery_status": job.delivery_status,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
    }


def _short_text(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    clean = " ".join(value.split())
    return clean if len(clean) <= limit else f"{clean[:limit - 1].rstrip()}…"


class CodingMixin:
    def coding_job_enqueue(
        self,
        *,
        mode: str,
        prompt: str,
        idempotency_key: str,
        repository_url: str | None = None,
        source_urls: list[str] | None = None,
        source_text: str | None = None,
        source_label: str | None = None,
        followups: list[str] | None = None,
    ) -> dict[str, Any]:
        capability = "coding.queue" if mode == "coding" else "research.run"
        self._capabilities().require(capability)
        tg_user_id = int(os.getenv("HERMES_OWNER_TELEGRAM_CHAT_ID", "0") or 0)
        if tg_user_id <= 0:
            raise RuntimeError("HERMES_OWNER_TELEGRAM_CHAT_ID is required")
        if followups:
            if source_text is not None or source_label is not None:
                raise ValueError("Follow-up coding jobs do not accept an attached text export.")
            jobs = self._coding_jobs().enqueue_chain(
                tg_user_id=tg_user_id,
                mode=mode,
                prompt=prompt,
                repository_url=repository_url,
                source_urls=list(source_urls or []),
                followups=followups,
                idempotency_key=idempotency_key,
            )
            payload = _value_payload(jobs[0])
            payload["followup_job_ids"] = [job.id for job in jobs[1:]]
            return payload
        return _value_payload(self._coding_jobs().enqueue(
            tg_user_id=tg_user_id,
            mode=mode,
            prompt=prompt,
            repository_url=repository_url,
            source_urls=list(source_urls or []),
            source_text=source_text,
            source_label=source_label,
            idempotency_key=idempotency_key,
        ))

    def coding_job_list(self, *, limit: int = 20, include_result: bool = False) -> dict[str, Any]:
        self._capabilities().require("coding.read")
        tg_user_id = self._coding_owner_id()
        items = self._coding_jobs().list_for_user(tg_user_id, limit=limit)
        if include_result:
            return {"items": [_value_payload(item) for item in items]}
        return {"items": [_coding_job_summary(item) for item in items]}

    def coding_job_get(self, *, job_id: int) -> dict[str, Any]:
        self._capabilities().require("coding.read")
        return _value_payload(self._coding_jobs().get_for_user(job_id, tg_user_id=self._coding_owner_id()))

    def _coding_owner_id(self) -> int:
        tg_user_id = int(os.getenv("HERMES_OWNER_TELEGRAM_CHAT_ID", "0") or 0)
        if tg_user_id <= 0:
            raise RuntimeError("HERMES_OWNER_TELEGRAM_CHAT_ID is required")
        return tg_user_id
