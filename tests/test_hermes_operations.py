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


def test_memory_and_zombie_parsers_keep_watchdog_logic_deterministic() -> None:
    total, available = parse_meminfo("MemTotal: 1000 kB\nMemAvailable: 250 kB\n")

    assert (total, available) == (1_024_000, 256_000)
    assert zombie_processes("S 1 2\nZ 42 43\nZs 9 10\n", parent_pid=42) == [43]
    assert zombie_processes("S 1 2\nZ 42 43\nZs 9 10\n") == [43, 10]


def test_profile_sync_script_preserves_runtime_state_and_live_config() -> None:
    script = (Path(__file__).resolve().parents[1] / "deploy" / "vps" / "sync_hermes_profile.sh").read_text(
        encoding="utf-8"
    )

    assert "Preserved live config.yaml" in script
    assert "SYNC_PROFILE_CONFIG" in script
    assert '"$PROFILE_DIR/auth.json"' not in script
