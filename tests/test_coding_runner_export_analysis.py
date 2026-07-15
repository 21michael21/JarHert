from __future__ import annotations

from scripts.coding_runner import run_once


class Queue:
    def __init__(self) -> None:
        self.completed: list[tuple[int, str, str]] = []

    def claim(self, _worker_id: str) -> dict[str, object]:
        return {
            "id": 41,
            "mode": "research",
            "prompt": "Выдели главные темы.",
            "repository_url": None,
            "source_urls": [],
            "source_text": "[1] Обсуждаем SQLite и ML.",
            "source_label": "mlphys.txt",
        }

    def complete(self, job_id: int, worker_id: str, output: str) -> None:
        self.completed.append((job_id, worker_id, output))

    def fail(self, _job_id: int, _worker_id: str, _error: str) -> None:  # pragma: no cover - assertion guard.
        raise AssertionError("research job must not fail")


class Worker:
    def __init__(self) -> None:
        self.task = None

    def run(self, task):
        self.task = task
        return type("Result", (), {"output": "Готовый разбор"})()


def test_runner_passes_temporary_export_text_to_the_isolated_research_task() -> None:
    queue = Queue()
    worker = Worker()

    assert run_once(client=queue, worker=worker, worker_id="mac") is True
    assert worker.task.source_text == "[1] Обсуждаем SQLite и ML."
    assert worker.task.source_label == "mlphys.txt"
    assert queue.completed == [(41, "mac", "Готовый разбор")]
