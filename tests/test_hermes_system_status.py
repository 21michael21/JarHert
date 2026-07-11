from __future__ import annotations

import json
import subprocess
from pathlib import Path

from hermes.native_tools.system_status import collect_system_status


def _runner(arguments: list[str], **_kwargs) -> subprocess.CompletedProcess[str]:
    if arguments[:4] == ["systemctl", "--user", "is-active", "hermes-gateway-jarhert.service"]:
        return subprocess.CompletedProcess(arguments, 0, stdout="active\n", stderr="")
    if arguments[:4] == ["systemctl", "--user", "is-active", "hermes-watchdog.timer"]:
        return subprocess.CompletedProcess(arguments, 0, stdout="active\n", stderr="")
    if arguments[:4] == ["systemctl", "--user", "is-active", "hermes-backup.timer"]:
        return subprocess.CompletedProcess(arguments, 0, stdout="active\n", stderr="")
    if arguments[:4] == ["systemctl", "--user", "show", "hermes-gateway-jarhert.service"]:
        return subprocess.CompletedProcess(arguments, 0, stdout="123\n", stderr="")
    if arguments[:2] == ["ps", "-eo"]:
        return subprocess.CompletedProcess(arguments, 0, stdout="S 1 2\nZ 123 124\n", stderr="")
    raise AssertionError(arguments)


def test_system_status_reports_operational_facts_without_personal_content(tmp_path: Path) -> None:
    profile = tmp_path / "profile"
    (profile / "cron").mkdir(parents=True)
    (profile / "state").mkdir()
    (profile / "cron" / "jobs.json").write_text(json.dumps({"jobs": [{"id": 1}, {"id": 2}]}), encoding="utf-8")
    ticker = profile / "cron" / "ticker_last_success"
    ticker.write_text("ok", encoding="utf-8")
    (profile / "state" / "jarhert-profile-revision.json").write_text(
        json.dumps({"jarhert_commit": "0123456789abcdef"}), encoding="utf-8"
    )
    backup = tmp_path / "backups"
    backup.mkdir()
    (backup / "jarhert-profile-20260711T120000Z.tar.gpg").write_bytes(b"encrypted")
    backup_secret = tmp_path / "backup.env"
    backup_secret.write_text("HERMES_BACKUP_PASSPHRASE=<redacted>\n", encoding="utf-8")
    backup_secret.chmod(0o600)
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal: 1000 kB\nMemAvailable: 250 kB\n", encoding="utf-8")

    status = collect_system_status(
        profile_home=profile,
        backup_dir=backup,
        backup_secret_path=backup_secret,
        command_runner=_runner,
        meminfo_path=meminfo,
        now=ticker.stat().st_mtime + 20,
    )

    assert status["gateway"] == {"active": True, "main_pid": 123}
    assert status["automation"] == {"watchdog_timer_active": True, "backup_timer_active": True}
    assert status["resources"]["zombie_children"] == [124]
    assert status["resources"]["memory_used_percent"] == 75.0
    assert status["cron"]["jobs"] == 2
    assert status["cron"]["last_success_age_seconds"] == 20
    assert status["backup"]["archives"] == 1
    assert status["backup"]["configured"] is True
    assert status["backup"]["secret_file_mode"] == "0600"
    assert status["profile"]["revision"] == "0123456789ab"


def test_system_status_marks_backup_unconfigured_without_reading_a_secret(tmp_path: Path) -> None:
    profile = tmp_path / "profile"
    profile.mkdir()
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal: 1000 kB\nMemAvailable: 500 kB\n", encoding="utf-8")

    status = collect_system_status(
        profile_home=profile,
        backup_dir=tmp_path / "missing-backups",
        backup_secret_path=tmp_path / "missing-backup.env",
        command_runner=_runner,
        meminfo_path=meminfo,
    )

    assert status["backup"]["configured"] is False
    assert status["backup"]["secret_file_mode"] is None


def test_profile_exposes_status_only_through_native_mcp() -> None:
    root = Path(__file__).resolve().parents[1]
    config = (root / "hermes" / "config.yaml").read_text(encoding="utf-8")
    soul = (root / "hermes" / "SOUL.md").read_text(encoding="utf-8")
    skill = (root / "hermes" / "skills" / "system-status" / "SKILL.md").read_text(encoding="utf-8")

    assert "- system_status" in config
    assert "mcp_jarhert_native_system_status" in soul
    assert "mcp_jarhert_native_system_status" in skill
