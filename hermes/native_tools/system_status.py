from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .github_mcp import github_mcp_status
from .operations import ARCHIVE_PREFIX, ARCHIVE_SUFFIX, parse_meminfo, zombie_processes


CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


def collect_system_status(
    *,
    profile_home: str | Path,
    unit: str = "hermes-gateway-jarhert.service",
    backup_dir: str | Path | None = None,
    backup_secret_path: str | Path | None = None,
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
    backup_secret = Path(backup_secret_path or Path.home() / ".config" / "jarhert" / "backup.env").expanduser()
    ticker = profile / "cron" / "ticker_last_success"
    database = profile / "data" / "personal-os.sqlite3"
    coding_queue = _coding_queue_status(database)
    zombies = zombie_processes(process_table, parent_pid=main_pid)
    return {
        "gateway": {"active": active, "main_pid": main_pid},
        "provider": _model_config(profile / "config.yaml"),
        "github_mcp": github_mcp_status(profile_home=profile),
        "coding_queue": coding_queue,
        "runtime": _runtime_health(gateway_active=active, coding_queue=coding_queue, zombies=zombies),
        "personal_summaries": _personal_summary_status(database),
        "automation": {
            "watchdog_timer_active": _systemctl_active("hermes-watchdog.timer", command_runner),
            "backup_timer_active": _systemctl_active("hermes-backup.timer", command_runner),
        },
        "resources": {
            "disk_free_gib": round(disk.free / (1024**3), 2),
            "memory_used_percent": round(100 * (1 - available_memory / total_memory), 1),
            "zombie_children": zombies,
        },
        "cron": {
            "jobs": _cron_job_count(profile / "cron" / "jobs.json"),
            "last_success_age_seconds": _age_seconds(ticker, current),
        },
        "backup": {
            "archives": len(archives),
            "latest_age_seconds": _age_seconds(archives[0], current) if archives else None,
            "configured": _is_private_backup_secret(backup_secret),
            "secret_file_mode": _file_mode(backup_secret),
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


def _file_mode(path: Path) -> str | None:
    try:
        return f"{path.stat().st_mode & 0o777:04o}" if path.is_file() else None
    except OSError:
        return None


def _is_private_backup_secret(path: Path) -> bool:
    return _file_mode(path) == "0600"


def _model_config(path: Path) -> dict[str, str]:
    result = {"name": "unknown", "model": "unknown"}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return result
    in_model = False
    for line in lines:
        if line.strip() == "model:":
            in_model = True
            continue
        if in_model and line and not line[0].isspace():
            break
        if not in_model:
            continue
        key, separator, value = line.strip().partition(":")
        if not separator:
            continue
        if key == "provider" and value.strip():
            result["name"] = value.strip().strip('"\'')
        if key == "default" and value.strip():
            result["model"] = value.strip().strip('"\'')
    return result


def _coding_queue_status(database: Path) -> dict[str, int | bool | str | None]:
    result: dict[str, int | bool | str | None] = {
        "available": False,
        "queued": 0,
        "running": 0,
        "failed": 0,
        "delivery_pending": 0,
        "worker_state": "unknown",
        "last_heartbeat_at": None,
    }
    if not database.is_file():
        return result
    try:
        with sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True) as connection:
            statuses = dict(connection.execute("SELECT status, COUNT(*) FROM native_coding_jobs GROUP BY status"))
            pending = connection.execute(
                "SELECT COUNT(*) FROM native_coding_jobs WHERE delivery_status IN ('pending', 'delivering') AND deliver_result = 1"
            ).fetchone()
            unresolved_failed = connection.execute(
                """
                SELECT COUNT(*) FROM native_coding_jobs
                WHERE status = 'failed' AND deliver_result = 1 AND COALESCE(delivery_status, 'pending') != 'delivered'
                """
            ).fetchone()
            columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(native_coding_jobs)")}
            heartbeat = (
                connection.execute("SELECT MAX(heartbeat_at) FROM native_coding_jobs WHERE heartbeat_at IS NOT NULL").fetchone()
                if "heartbeat_at" in columns
                else None
            )
    except (OSError, sqlite3.Error):
        return result
    result["available"] = True
    for status in ("queued", "running"):
        result[status] = int(statuses.get(status, 0))
    # A delivered failure stays in the job history but is not a live runner incident.
    result["failed"] = int(unresolved_failed[0]) if unresolved_failed else 0
    result["delivery_pending"] = int(pending[0]) if pending else 0
    result["last_heartbeat_at"] = str(heartbeat[0]) if heartbeat and heartbeat[0] else None
    result["worker_state"] = (
        "busy" if int(result["running"]) else "attention" if int(result["failed"]) else "idle"
    )
    return result


def _runtime_health(
    *,
    gateway_active: bool,
    coding_queue: dict[str, int | bool | str | None],
    zombies: list[int],
) -> dict[str, object]:
    reasons: list[str] = []
    if not gateway_active:
        reasons.append("gateway_inactive")
    if int(coding_queue["failed"] or 0):
        reasons.append("coding_failed")
    if zombies:
        reasons.append("zombie_children")
    return {
        "state": "offline" if not gateway_active else "attention" if reasons else "healthy",
        "reasons": reasons,
    }


def _personal_summary_status(database: Path) -> dict[str, dict[str, str | None] | bool]:
    result: dict[str, dict[str, str | None] | bool] = {
        "available": False,
        "daily": {"status": None, "updated_at": None},
        "weekly": {"status": None, "updated_at": None},
    }
    if not database.is_file():
        return result
    try:
        with sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True) as connection:
            rows = connection.execute(
                """
                SELECT summary_type, status, updated_at
                FROM personal_summary_deliveries
                WHERE id IN (
                    SELECT MAX(id) FROM personal_summary_deliveries GROUP BY summary_type
                )
                """
            ).fetchall()
    except (OSError, sqlite3.Error):
        return result
    result["available"] = True
    for summary_type, status, updated_at in rows:
        if summary_type in {"daily", "weekly"}:
            result[str(summary_type)] = {"status": str(status), "updated_at": str(updated_at)}
    return result


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
