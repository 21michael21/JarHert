from __future__ import annotations

import json
import shlex
import subprocess
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable


Execute = Callable[..., subprocess.CompletedProcess[str]]


class SshNativeCodingQueueClient:
    """Poll the private profile queue over an existing SSH key, never a public port."""

    def __init__(
        self,
        ssh_host: str,
        *,
        remote_profile: str = "/home/deploy/.hermes/profiles/jarhert",
        remote_python: str = "/home/deploy/.hermes/hermes-agent/venv/bin/python",
        execute: Execute = subprocess.run,
    ) -> None:
        self.ssh_host = _required(ssh_host, "SSH host", 240)
        self.remote_profile = _required(remote_profile, "Remote profile", 500)
        self.remote_python = _required(remote_python, "Remote Python", 500)
        self.execute = execute

    def claim(self, worker_id: str) -> dict[str, Any] | None:
        return self._call("claim", {"worker_id": worker_id})

    def ping(self) -> bool:
        return bool(self._call("ping", {})["ok"])

    def heartbeat(self, job_id: int, worker_id: str) -> bool:
        return bool(self._call("heartbeat", {"job_id": int(job_id), "worker_id": worker_id})["ok"])

    def complete(self, job_id: int, worker_id: str, result_text: str) -> None:
        self._call("complete", {"job_id": int(job_id), "worker_id": worker_id, "result_text": result_text})

    def fail(self, job_id: int, worker_id: str, error: str) -> None:
        self._call("fail", {"job_id": int(job_id), "worker_id": worker_id, "error": error})

    def _call(self, operation: str, payload: dict[str, Any]) -> Any:
        command = (
            f"HERMES_HOME={shlex.quote(self.remote_profile)} "
            f"{shlex.quote(self.remote_python)} "
            f"{shlex.quote(str(Path(self.remote_profile) / 'scripts' / 'coding_queue_cli.py'))} "
            f"{shlex.quote(operation)}"
        )
        result = self.execute(
            ["ssh", "-o", "BatchMode=yes", self.ssh_host, command],
            input=json.dumps(payload, ensure_ascii=False),
            text=True,
            capture_output=True,
            timeout=40,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(f"SSH coding queue unavailable (exit {result.returncode}).")
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise RuntimeError("SSH coding queue returned invalid JSON.") from error


def payload(value: Any) -> dict[str, Any]:
    return asdict(value)


def _required(value: str, label: str, limit: int) -> str:
    clean = str(value or "").strip()
    if not clean or len(clean) > limit:
        raise ValueError(f"{label} is invalid.")
    return clean
