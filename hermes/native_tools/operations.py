from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable


BACKUP_FILES = (
    ".env",
    "auth.json",
    "config.yaml",
    "distribution.yaml",
    "SOUL.md",
    "AGENTS.md",
    "cron/jobs.json",
    "data/telegram-user.session",
)
SQLITE_FILES = (
    "state.db",
    "data/personal-os.sqlite3",
)
ARCHIVE_PREFIX = "jarhert-profile-"
ARCHIVE_SUFFIX = ".tar.gpg"


@dataclass(frozen=True)
class BackupRetention:
    daily: int = 7
    weekly: int = 4
    monthly: int = 3

    def __post_init__(self) -> None:
        if min(self.daily, self.weekly, self.monthly) < 0:
            raise ValueError("Backup retention values must be non-negative.")


@dataclass(frozen=True)
class BackupResult:
    archive: Path
    removed: tuple[Path, ...]


def snapshot_profile(profile_home: str | Path, destination: str | Path) -> Path:
    """Build a consistent, unencrypted staging copy of recoverable profile state."""
    source = Path(profile_home).expanduser().resolve()
    target = Path(destination).resolve()
    if not source.is_dir():
        raise ValueError(f"Hermes profile does not exist: {source}")
    target.mkdir(parents=True, exist_ok=True)
    for relative in BACKUP_FILES:
        _copy_optional(source, target, relative)
    for relative in SQLITE_FILES:
        database = source / relative
        if not database.is_file():
            raise ValueError(f"Required Hermes database is missing: {database}")
        snapshot = target / relative
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        _snapshot_sqlite(database, snapshot)
    return target


def create_encrypted_backup(
    *,
    profile_home: str | Path,
    backup_dir: str | Path,
    passphrase: str,
    retention: BackupRetention = BackupRetention(),
    now: datetime | None = None,
    gpg_command: str = "gpg",
) -> BackupResult:
    """Encrypt a profile snapshot with GnuPG and rotate older archives."""
    if not passphrase:
        raise ValueError("Backup passphrase is empty.")
    backup_root = Path(backup_dir).expanduser().resolve()
    backup_root.mkdir(parents=True, exist_ok=True)
    archive = _next_archive_path(backup_root, now=now)
    with tempfile.TemporaryDirectory(prefix="jarhert-backup-") as temporary:
        staging = snapshot_profile(profile_home, Path(temporary) / "profile")
        plain_tar = Path(temporary) / "profile.tar"
        _create_tar(staging, plain_tar)
        _encrypt_with_gpg(plain_tar, archive, passphrase=passphrase, gpg_command=gpg_command)
    os.chmod(archive, 0o600)
    removed = rotate_backups(backup_root, retention=retention)
    return BackupResult(archive=archive, removed=tuple(removed))


def restore_encrypted_backup(
    *,
    archive: str | Path,
    destination: str | Path,
    passphrase: str,
    gpg_command: str = "gpg",
) -> Path:
    """Restore only into an empty destination; never overwrite a live profile."""
    source = Path(archive).expanduser().resolve()
    target = Path(destination).expanduser().resolve()
    if not source.is_file():
        raise ValueError(f"Backup archive does not exist: {source}")
    if target.exists() and any(target.iterdir()):
        raise ValueError(f"Restore destination must be empty: {target}")
    if not passphrase:
        raise ValueError("Backup passphrase is empty.")
    target.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="jarhert-restore-") as temporary:
        plain_tar = Path(temporary) / "profile.tar"
        _decrypt_with_gpg(source, plain_tar, passphrase=passphrase, gpg_command=gpg_command)
        _safe_extract_tar(plain_tar, target)
    return target


def verify_restored_profile(profile_home: str | Path) -> dict[str, str]:
    root = Path(profile_home).expanduser().resolve()
    result: dict[str, str] = {}
    for relative in SQLITE_FILES:
        database = root / relative
        if not database.is_file():
            raise ValueError(f"Restored database is missing: {database}")
        with sqlite3.connect(database) as connection:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise ValueError(f"SQLite integrity check failed for {relative}: {integrity}")
        result[relative] = integrity
    return result


def rotate_backups(backup_dir: str | Path, *, retention: BackupRetention) -> list[Path]:
    root = Path(backup_dir).expanduser().resolve()
    archives = sorted(
        (item for item in root.glob(f"{ARCHIVE_PREFIX}*{ARCHIVE_SUFFIX}") if _archive_datetime(item) is not None),
        key=lambda item: _archive_datetime(item) or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    retained: set[Path] = set()
    retained.update(_retain_by_bucket(archives, retention.daily, lambda value: _archive_datetime(value).date()))
    retained.update(
        _retain_by_bucket(
            archives,
            retention.weekly,
            lambda value: _archive_datetime(value).isocalendar()[:2],
        )
    )
    retained.update(
        _retain_by_bucket(archives, retention.monthly, lambda value: _archive_datetime(value).strftime("%Y-%m"))
    )
    removed: list[Path] = []
    for archive in archives:
        if archive not in retained:
            archive.unlink()
            removed.append(archive)
    return removed


def parse_meminfo(text: str) -> tuple[int, int]:
    values: dict[str, int] = {}
    for line in text.splitlines():
        key, separator, raw_value = line.partition(":")
        if not separator:
            continue
        parts = raw_value.split()
        if parts and parts[0].isdigit():
            values[key] = int(parts[0]) * 1024
    total = values.get("MemTotal")
    available = values.get("MemAvailable")
    if total is None or available is None:
        raise ValueError("/proc/meminfo is missing MemTotal or MemAvailable")
    return total, available


def zombie_processes(text: str, *, parent_pid: int | None = None) -> list[int]:
    zombies: list[int] = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) != 3:
            continue
        status, parent, pid = parts
        if not status.startswith("Z") or not parent.isdigit() or not pid.isdigit():
            continue
        if parent_pid is None or int(parent) == parent_pid:
            zombies.append(int(pid))
    return zombies


def _copy_optional(source: Path, target: Path, relative: str) -> None:
    current = source / relative
    if not current.is_file():
        return
    destination = target / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(current, destination)
    os.chmod(destination, 0o600)


def _snapshot_sqlite(source: Path, destination: Path) -> None:
    with sqlite3.connect(source) as read_connection:
        with sqlite3.connect(destination) as write_connection:
            read_connection.backup(write_connection)
    os.chmod(destination, 0o600)


def _create_tar(source: Path, destination: Path) -> None:
    with tarfile.open(destination, "w") as archive:
        for path in sorted(source.rglob("*")):
            archive.add(path, arcname=path.relative_to(source), recursive=False)
    os.chmod(destination, 0o600)


def _encrypt_with_gpg(source: Path, destination: Path, *, passphrase: str, gpg_command: str) -> None:
    result = subprocess.run(
        [
            gpg_command,
            "--batch",
            "--yes",
            "--pinentry-mode",
            "loopback",
            "--passphrase-fd",
            "0",
            "--symmetric",
            "--cipher-algo",
            "AES256",
            "--output",
            str(destination),
            str(source),
        ],
        input=f"{passphrase}\n",
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        destination.unlink(missing_ok=True)
        raise RuntimeError(f"GPG backup failed: {_command_error(result)}")


def _decrypt_with_gpg(source: Path, destination: Path, *, passphrase: str, gpg_command: str) -> None:
    result = subprocess.run(
        [
            gpg_command,
            "--batch",
            "--yes",
            "--pinentry-mode",
            "loopback",
            "--passphrase-fd",
            "0",
            "--decrypt",
            "--output",
            str(destination),
            str(source),
        ],
        input=f"{passphrase}\n",
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        destination.unlink(missing_ok=True)
        raise RuntimeError(f"GPG restore failed: {_command_error(result)}")


def _safe_extract_tar(archive_path: Path, destination: Path) -> None:
    root = destination.resolve()
    with tarfile.open(archive_path, "r") as archive:
        for member in archive.getmembers():
            candidate = (root / member.name).resolve()
            if candidate != root and root not in candidate.parents:
                raise ValueError("Backup archive contains an unsafe path.")
        archive.extractall(root, filter="data")


def _next_archive_path(root: Path, *, now: datetime | None) -> Path:
    timestamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    candidate = root / f"{ARCHIVE_PREFIX}{timestamp}{ARCHIVE_SUFFIX}"
    counter = 1
    while candidate.exists():
        candidate = root / f"{ARCHIVE_PREFIX}{timestamp}-{counter}{ARCHIVE_SUFFIX}"
        counter += 1
    return candidate


def _archive_datetime(path: Path) -> datetime | None:
    stem = path.name.removeprefix(ARCHIVE_PREFIX).removesuffix(ARCHIVE_SUFFIX).split("-", 1)[0]
    try:
        return datetime.strptime(stem, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _retain_by_bucket(
    archives: Iterable[Path],
    count: int,
    bucket: Callable[[Path], object],
) -> set[Path]:
    retained: set[Path] = set()
    seen: set[object] = set()
    for archive in archives:
        value = bucket(archive)
        if value in seen or len(seen) >= count:
            continue
        seen.add(value)
        retained.add(archive)
    return retained


def _command_error(result: subprocess.CompletedProcess[str]) -> str:
    return (result.stderr or result.stdout or f"exit {result.returncode}").strip()[:300]
