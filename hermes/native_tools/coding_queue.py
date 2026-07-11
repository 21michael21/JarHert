from __future__ import annotations

import json
import os
from urllib.request import Request, urlopen


class RemoteCodingQueueClient:
    def __init__(self, base_url: str, token: str) -> None:
        if not base_url.strip() or not token.strip():
            raise RuntimeError("JARHERT_BACKEND_URL and ASSISTANT_SERVICE_TOKEN are required")
        self.base_url = base_url.rstrip("/")
        self.token = token

    @classmethod
    def from_env(cls) -> "RemoteCodingQueueClient":
        return cls(
            os.getenv("JARHERT_BACKEND_URL", ""),
            os.getenv("ASSISTANT_SERVICE_TOKEN", ""),
        )

    def enqueue(self, **payload):
        return self._request("/api/coding/jobs", payload)

    def claim(self, worker_id: str):
        return self._request("/api/coding/jobs/claim", {"worker_id": worker_id})

    def heartbeat(self, job_id: int, worker_id: str) -> None:
        self._request(f"/api/coding/jobs/{job_id}/heartbeat", {"worker_id": worker_id})

    def complete(self, job_id: int, worker_id: str, result_text: str) -> None:
        self._request(
            f"/api/coding/jobs/{job_id}/complete",
            {"worker_id": worker_id, "result_text": result_text},
        )

    def fail(self, job_id: int, worker_id: str, error: str) -> None:
        self._request(f"/api/coding/jobs/{job_id}/fail", {"worker_id": worker_id, "error": error[:500]})

    def _request(self, path: str, payload: dict[str, object]):
        request = Request(
            f"{self.base_url}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
