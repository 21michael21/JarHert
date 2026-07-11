from __future__ import annotations

from pathlib import Path


def test_backup_timer_requires_external_secret_and_runs_restore_proof() -> None:
    root = Path(__file__).resolve().parents[1]
    service = (root / "deploy" / "vps" / "systemd" / "hermes-backup.service").read_text(encoding="utf-8")
    timer = (root / "deploy" / "vps" / "systemd" / "hermes-backup.timer").read_text(encoding="utf-8")
    runner = (root / "hermes" / "scripts" / "backup_and_verify.sh").read_text(encoding="utf-8")

    assert "ConditionPathExists=%h/.config/jarhert/backup.env" in service
    assert "EnvironmentFile=%h/.config/jarhert/backup.env" in service
    assert "OnCalendar=*-*-* 03:15:00" in timer
    assert "verify --archive" in runner
    assert "HERMES_BACKUP_PASSPHRASE" not in runner
