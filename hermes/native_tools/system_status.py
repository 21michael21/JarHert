from __future__ import annotations

import json
import shutil
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .operations import ARCHIVE_PREFIX, ARCHIVE_SUFFIX, parse_meminfo, zombie_processes


CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


def collect_system_status(
    *,
    profile_home: str | Path,
    unit: str = "hermes-gateway-jarhert.service",
    backup_dir: str | Path | None = None,
    command_runner: CommandRunner = subprocess.run,
    meminfo_path: str | Path = "/proc/meminfo",
    now: float | None = None,
) -> dict[str, Any]:
    """Return an operational snapshot without reading personal content or secrets."""
    profile = Path(profile_home).expanduser().resolve()
    current = time.time() if now is None else now
    active = _systemctl_active(unit, command_runner)
    main_pid = _systemctl_main_pid(unit, command_runner) if active else None
    process_table = _run(command_runner, ["ps", "-eo", "stat=,ppid=,pid="]).stdout
    total_memory, available_memory = parse_meminfo(Path(meminfo_path).read_text(encoding="utf-8"))
    disk = shutil.disk_usage(profile)
    archives = _backup_archives(backup_dir or profile.parent.parent / "backups" / "jarhert")
    ticker = profile / "cron" / "ticker_last_success"
    return {
        "gateway": {"active": active, "main_pid": main_pid},
        "resources": {
            "disk_free_gib": round(disk.free / (1024**3), 2),
            "memory_used_percent": round(100 * (1 - available_memory / total_memory), 1),
            "zombie_children": zombie_processes(process_table, parent_pid=main_pid),
        },
        "cron": {
            "jobs": _cron_job_count(profile / "cron" / "jobs.json"),
            "last_success_age_seconds": _age_seconds(ticker, current),
        },
        "backup": {
            "archives": len(archives),
            "latest_age_seconds": _age_seconds(archives[0], current) if archives else None,
        },
        "profile": {"revision": _profile_revision(profile / "state" / "jarhert-profile-revision.json")},
    }


def _systemctl_active(unit: str, runner: CommandRunner) -> bool:
    return _run(runner, ["systemctl", "--user", "is-active", unit]).stdout.strip() == "active"


def _systemctl_main_pid(unit: str, runner: CommandRunner) -> int | None:
    output = _run(runner, ["systemctl", "--user", "show", unit, "--property", "MainPID", "--value"]).stdout.strip()
    return int(output) if output.isdigit() and int(output) > 0 else None


def _run(runner: CommandRunner, arguments: list[str]) -> subprocess.CompletedProcess[str]:
    return runner(arguments, text=True, capture_output=True, check=False)


def _backup_archives(root: str | Path) -> list[Path]:
    directory = Path(root).expanduser()
    if not directory.is_dir():
        return []
    return sorted(
        directory.glob(f"{ARCHIVE_PREFIX}*{ARCHIVE_SUFFIX}"),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )


def _cron_job_count(path: Path) -> int:
    if not path.is_file():
        return 0
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    if isinstance(payload, list):
        return len(payload)
    return len(payload.get("jobs", [])) if isinstance(payload, dict) else 0


def _profile_revision(path: Path) -> str:
    if not path.is_file():
        return "unknown"
    try:
        value = json.loads(path.read_text(encoding="utf-8")).get("jarhert_commit")
    except (OSError, json.JSONDecodeError):
        return "unknown"
    return str(value)[:12] if value else "unknown"


def _age_seconds(path: Path, current: float) -> int | None:
    if not path.is_file():
        return None
    return max(0, int(current - path.stat().st_mtime))
