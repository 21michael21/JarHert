from __future__ import annotations

from hermes.native_tools.sandbox_worker import SandboxResult
from scripts.coding_runner import run_once


class QueueClient:
    def __init__(self) -> None:
        self.completed = []
        self.failed = []

    def claim(self, worker_id):
        return {
            "id": 9,
            "mode": "coding",
            "prompt": "Добавь тест",
            "repository_url": "https://github.com/example/repo",
            "source_urls": [],
        }

    def complete(self, job_id, worker_id, result_text):
        self.completed.append((job_id, worker_id, result_text))

    def fail(self, job_id, worker_id, error):
        self.failed.append((job_id, worker_id, error))


class Worker:
    def run(self, task):
        assert task.repository_url == "https://github.com/example/repo"
        return SandboxResult(output="tests passed", mode=task.mode)


def test_remote_runner_claims_sandbox_job_and_returns_result() -> None:
    client = QueueClient()

    worked = run_once(client=client, worker=Worker(), worker_id="mac-a")

    assert worked is True
    assert client.completed == [(9, "mac-a", "tests passed")]
    assert client.failed == []
