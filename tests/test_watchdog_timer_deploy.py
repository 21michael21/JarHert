from __future__ import annotations

from pathlib import Path


def test_watchdog_units_are_user_safe_and_periodic() -> None:
    root = Path(__file__).resolve().parents[1]
    service = (root / "deploy" / "vps" / "systemd" / "hermes-watchdog.service").read_text(encoding="utf-8")
    timer = (root / "deploy" / "vps" / "systemd" / "hermes-watchdog.timer").read_text(encoding="utf-8")
    installer = (root / "deploy" / "vps" / "install_watchdog_timer.sh").read_text(encoding="utf-8")

    assert "systemctl --user" in installer
    assert "watchdog.py" in service
    assert "--restart-inactive" in service
    assert "--restart-inactive-timers" in service
    assert "--timer hermes-backup.timer" in service
    assert "--timer hermes-telegram-export-cleanup.timer" in service
    assert "ConditionPathExists=!%h/.hermes/profiles/jarhert/state/maintenance" in service
    assert "OnUnitActiveSec=5min" in timer
    assert "Persistent=true" in timer
