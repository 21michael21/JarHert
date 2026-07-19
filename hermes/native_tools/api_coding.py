"""Coding-queue methods of NativeToolsAPI, split out of the god object."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from .api_payload import coding_job_summary, value_payload

if TYPE_CHECKING:
    from .mcp_api import NativeToolsAPI


class CodingJobsMixin:
    if TYPE_CHECKING:
        _capabilities: "NativeToolsAPI._capabilities"
        _coding_jobs: "NativeToolsAPI._coding_jobs"
        _coding_owner_id: "NativeToolsAPI._coding_owner_id"

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
            payload = value_payload(jobs[0])
            payload["followup_job_ids"] = [job.id for job in jobs[1:]]
            return payload
        return value_payload(self._coding_jobs().enqueue(
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
            return {"items": [value_payload(item) for item in items]}
        return {"items": [coding_job_summary(item) for item in items]}

    def coding_job_get(self, *, job_id: int) -> dict[str, Any]:
        self._capabilities().require("coding.read")
        return value_payload(self._coding_jobs().get_for_user(job_id, tg_user_id=self._coding_owner_id()))
