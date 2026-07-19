from __future__ import annotations

import sys

import pytest

from scripts import coding_runner


class _FlakyQueue:
    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.calls = 0

    def claim(self, _worker_id: str):
        self.calls += 1
        if self.calls <= self.failures:
            raise RuntimeError("SSH coding queue unavailable (exit 255).")
        return None

    def ping(self) -> bool:
        return True


class _Worker:
    def preflight(self) -> None:
        return None

    def run(self, _task):  # pragma: no cover - no job reaches it in this test.
        raise AssertionError("no job expected")


def _patch_runtime(monkeypatch: pytest.MonkeyPatch, queue: _FlakyQueue) -> None:
    monkeypatch.setattr(coding_runner, "SshNativeCodingQueueClient", lambda *args, **kwargs: queue)
    monkeypatch.setattr(coding_runner, "coding_worker_from_environment", lambda: _Worker())
    monkeypatch.setattr(coding_runner.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        sys,
        "argv",
        ["coding_runner.py", "--queue-ssh", "deploy@example.test", "--worker-id", "mac-main", "--once"],
    )


def test_runner_survives_flaky_network_and_finishes_cleanly(monkeypatch: pytest.MonkeyPatch) -> None:
    queue = _FlakyQueue(failures=3)
    _patch_runtime(monkeypatch, queue)

    assert coding_runner.main() == 0
    assert queue.calls == 4


def test_runner_once_still_raises_persistent_queue_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    queue = _FlakyQueue(failures=100)
    _patch_runtime(monkeypatch, queue)

    with pytest.raises(RuntimeError, match="exit 255"):
        coding_runner.main()
