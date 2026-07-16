from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from hermes.native_tools.operations import (
    ARCHIVE_PREFIX,
    ARCHIVE_SUFFIX,
    BackupRetention,
    parse_meminfo,
    rotate_backups,
    snapshot_profile,
    verify_restored_profile,
    write_backup_secret,
    zombie_processes,
)


def _profile(root: Path) -> Path:
    profile = root / "profile"
    (profile / "data").mkdir(parents=True)
    for relative in ("state.db", "data/personal-os.sqlite3"):
        with sqlite3.connect(profile / relative) as connection:
            connection.execute("CREATE TABLE canary (value TEXT)")
            connection.execute("INSERT INTO canary VALUES ('ok')")
    (profile / ".env").write_text("SECRET=not-for-output\n", encoding="utf-8")
    (profile / "auth.json").write_text('{"private":true}', encoding="utf-8")
    return profile


def test_profile_snapshot_uses_sqlite_backup_and_integrity_check(tmp_path: Path) -> None:
    profile = _profile(tmp_path)
    staging = snapshot_profile(profile, tmp_path / "staging")

    assert (staging / ".env").read_text(encoding="utf-8") == "SECRET=not-for-output\n"
    assert verify_restored_profile(staging) == {
        "state.db": "ok",
        "data/personal-os.sqlite3": "ok",
    }
    with sqlite3.connect(staging / "state.db") as connection:
        assert connection.execute("SELECT value FROM canary").fetchone()[0] == "ok"


def test_profile_snapshot_requires_both_recoverable_databases(tmp_path: Path) -> None:
    profile = tmp_path / "profile"
    profile.mkdir()

    with pytest.raises(ValueError, match="Required Hermes database"):
        snapshot_profile(profile, tmp_path / "staging")


def test_backup_rotation_keeps_daily_weekly_and_monthly_recovery_points(tmp_path: Path) -> None:
    archive_dates = (
        "20260101T010101Z",
        "20260102T010101Z",
        "20260108T010101Z",
        "20260201T010101Z",
        "20260301T010101Z",
        "20260401T010101Z",
    )
    archives = [tmp_path / f"{ARCHIVE_PREFIX}{stamp}{ARCHIVE_SUFFIX}" for stamp in archive_dates]
    for archive in archives:
        archive.write_bytes(b"encrypted")

    removed = rotate_backups(tmp_path, retention=BackupRetention(daily=1, weekly=1, monthly=2))

    assert archives[-1] not in removed
    assert archives[-2] not in removed
    assert archives[0] in removed


def test_backup_secret_setup_writes_private_shell_safe_environment_file(tmp_path: Path) -> None:
    destination = tmp_path / "config" / "backup.env"

    written = write_backup_secret(
        destination,
        passphrase="test backup phrase with spaces and $dollar",
    )

    assert written == destination
    assert destination.stat().st_mode & 0o777 == 0o600
    assert destination.parent.stat().st_mode & 0o777 == 0o700
    assert destination.read_text(encoding="utf-8") == "HERMES_BACKUP_PASSPHRASE='test backup phrase with spaces and $dollar'\n"


def test_backup_secret_setup_refuses_short_or_accidental_overwrite(tmp_path: Path) -> None:
    destination = tmp_path / "backup.env"

    with pytest.raises(ValueError, match="at least 20"):
        write_backup_secret(destination, passphrase="too-short")

    write_backup_secret(destination, passphrase="a sufficiently long passphrase")
    with pytest.raises(ValueError, match="already exists"):
        write_backup_secret(destination, passphrase="another sufficiently long passphrase")


def test_memory_and_zombie_parsers_keep_watchdog_logic_deterministic() -> None:
    total, available = parse_meminfo("MemTotal: 1000 kB\nMemAvailable: 250 kB\n")

    assert (total, available) == (1_024_000, 256_000)
    assert zombie_processes("S 1 2\nZ 42 43\nZs 9 10\n", parent_pid=42) == [43]
    assert zombie_processes("S 1 2\nZ 42 43\nZs 9 10\n") == [43, 10]


def test_profile_sync_script_preserves_runtime_state_and_live_config() -> None:
    script = (Path(__file__).resolve().parents[1] / "deploy" / "vps" / "sync_hermes_profile.sh").read_text(
        encoding="utf-8"
    )

    assert "Merged safe JarHert defaults while preserving live config.yaml" in script
    assert "SYNC_PROFILE_CONFIG" in script
    assert "HERMES_NATIVE_SEND_COMMAND" in script
    assert "HERMES_NATIVE_SEND_COMMAND='%s -m hermes_cli.main'" in script
    assert "printf 'HERMES_NATIVE_SEND_COMMAND=%q" not in script
    assert "tools disable --platform telegram" in script
    assert "terminal file code_execution browser computer_use delegation cronjob" in script
    assert 'pip install --editable "$HERMES_SOURCE_DIR[mcp]"' in script
    assert '"$HERMES_SOURCE_DIR[mcp]"' in script
    assert "hermes-gateway-jarhert.service.d/override.conf" in script
    assert "systemctl --user daemon-reload" in script
    assert '"$PROFILE_DIR/auth.json"' not in script


def test_gateway_stop_timeout_override_is_bounded() -> None:
    override = (Path(__file__).resolve().parents[1] / "deploy" / "vps" / "systemd" / "hermes-gateway-jarhert.override.conf").read_text(
        encoding="utf-8"
    )

    assert "TimeoutStopSec=90s" in override
    assert "HERMES_RESTART_DRAIN_TIMEOUT=60" in override


def test_versioned_telegram_profile_is_quiet_and_final_answer_first() -> None:
    config = (Path(__file__).resolve().parents[1] / "hermes" / "config.yaml").read_text(encoding="utf-8")

    assert "busy_input_mode: interrupt" in config
    assert "busy_ack_enabled: false" in config
    assert "interim_assistant_messages: false" in config
    assert "long_running_notifications: false" in config
    assert "cleanup_progress: true" in config
