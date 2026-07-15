from __future__ import annotations

import subprocess
from pathlib import Path

from hermes.scripts import watchdog


def _completed(stdout: str) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")


def test_watchdog_recovers_only_explicit_inactive_timers(tmp_path, monkeypatch) -> None:
    memory = tmp_path / "meminfo"
    memory.write_text("MemTotal: 1000 kB\nMemAvailable: 800 kB\n", encoding="utf-8")
    states = {
        "hermes-gateway-jarhert.service": True,
        "hermes-backup.timer": False,
        "hermes-daily-brief.timer": True,
    }
    calls: list[tuple[str, str]] = []

    def fake_systemctl(*arguments: str, check: bool = False):
        action, unit = arguments[:2]
        calls.append((action, unit))
        if action == "is-active":
            return _completed("active\n" if states.get(unit, False) else "inactive\n")
        if action == "show":
            return _completed("4242\n")
        if action in {"start", "restart"}:
            states[unit] = True
            return _completed("")
        raise AssertionError(arguments)

    monkeypatch.setattr(watchdog, "_systemctl", fake_systemctl)
    monkeypatch.setattr(watchdog, "_command", lambda *_args, **_kwargs: _completed("S 1 2\n"))

    args = watchdog.build_parser().parse_args(
        [
            "--path",
            str(tmp_path),
            "--meminfo-path",
            str(memory),
            "--timer",
            "hermes-backup.timer",
            "--timer",
            "hermes-daily-brief.timer",
            "--restart-inactive-timers",
        ]
    )

    result = watchdog.run(args)

    assert result["healthy"] is True
    assert result["restarted_timers"] == ["hermes-backup.timer"]
    assert result["timer_status"] == {
        "hermes-backup.timer": True,
        "hermes-daily-brief.timer": True,
    }
    assert ("start", "hermes-backup.timer") in calls
    assert ("restart", "hermes-gateway-jarhert.service") not in calls


def test_watchdog_reports_inactive_timer_without_restarting_it(tmp_path, monkeypatch) -> None:
    memory = tmp_path / "meminfo"
    memory.write_text("MemTotal: 1000 kB\nMemAvailable: 800 kB\n", encoding="utf-8")

    def fake_systemctl(*arguments: str, check: bool = False):
        action, unit = arguments[:2]
        if action == "is-active":
            return _completed("inactive\n" if unit == "hermes-backup.timer" else "active\n")
        if action == "show":
            return _completed("4242\n")
        raise AssertionError(arguments)

    monkeypatch.setattr(watchdog, "_systemctl", fake_systemctl)
    monkeypatch.setattr(watchdog, "_command", lambda *_args, **_kwargs: _completed("S 1 2\n"))

    args = watchdog.build_parser().parse_args(
        ["--path", str(tmp_path), "--meminfo-path", str(memory), "--timer", "hermes-backup.timer"]
    )

    result = watchdog.run(args)

    assert result["healthy"] is False
    assert result["restarted_timers"] == []
    assert result["timer_status"] == {"hermes-backup.timer": False}
