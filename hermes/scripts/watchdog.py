from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from native_tools.operations import parse_meminfo, zombie_processes
else:
    from ..native_tools.operations import parse_meminfo, zombie_processes


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check the Hermes gateway without touching healthy processes.")
    parser.add_argument("--unit", default="hermes-gateway-jarhert.service")
    parser.add_argument("--path", type=Path, default=Path.home())
    parser.add_argument("--meminfo-path", type=Path, default=Path("/proc/meminfo"))
    parser.add_argument("--timer", action="append", default=[])
    parser.add_argument("--min-disk-free-gib", type=float, default=5.0)
    parser.add_argument("--max-memory-percent", type=float, default=90.0)
    parser.add_argument("--restart-inactive", action="store_true")
    parser.add_argument("--restart-inactive-timers", action="store_true")
    parser.add_argument("--fail-on-zombies", action="store_true")
    return parser


def run(args: argparse.Namespace) -> dict[str, object]:
    active = _unit_active(args.unit)
    restarted = False
    if not active and args.restart_inactive:
        _systemctl("restart", args.unit, check=True)
        active = _unit_active(args.unit)
        restarted = active
    timer_status, restarted_timers = _check_timers(
        args.timer,
        restart_inactive=bool(args.restart_inactive_timers),
    )
    main_pid = _main_pid(args.unit) if active else None
    disk = shutil.disk_usage(args.path)
    disk_free_gib = round(disk.free / (1024**3), 2)
    total_memory, available_memory = parse_meminfo(args.meminfo_path.read_text(encoding="utf-8"))
    memory_used_percent = round(100 * (1 - available_memory / total_memory), 1)
    process_table = _command(["ps", "-eo", "stat=,ppid=,pid="]).stdout
    zombies = zombie_processes(process_table, parent_pid=main_pid)
    healthy = (
        active
        and main_pid is not None
        and disk_free_gib >= args.min_disk_free_gib
        and memory_used_percent <= args.max_memory_percent
        and (not args.fail_on_zombies or not zombies)
        and all(timer_status.values())
    )
    return {
        "healthy": healthy,
        "unit": args.unit,
        "active": active,
        "main_pid": main_pid,
        "restarted": restarted,
        "timer_status": timer_status,
        "restarted_timers": restarted_timers,
        "disk_free_gib": disk_free_gib,
        "memory_used_percent": memory_used_percent,
        "zombie_children": zombies,
    }


def _systemctl(*arguments: str, check: bool = False) -> subprocess.CompletedProcess[str]:
    return _command(["systemctl", "--user", *arguments], check=check)


def _unit_active(unit: str) -> bool:
    return _systemctl("is-active", unit).stdout.strip() == "active"


def _check_timers(timers: list[str], *, restart_inactive: bool) -> tuple[dict[str, bool], list[str]]:
    status: dict[str, bool] = {}
    restarted: list[str] = []
    for timer in dict.fromkeys(str(item).strip() for item in timers if str(item).strip()):
        active = _unit_active(timer)
        if not active and restart_inactive:
            _systemctl("start", timer, check=True)
            active = _unit_active(timer)
            if active:
                restarted.append(timer)
        status[timer] = active
    return status, restarted


def _main_pid(unit: str) -> int | None:
    output = _systemctl("show", unit, "--property", "MainPID", "--value", check=True).stdout.strip()
    return int(output) if output.isdigit() and int(output) > 0 else None


def _command(arguments: list[str], *, check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(arguments, text=True, capture_output=True, check=check)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = run(args)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(json.dumps({"healthy": False, "error": str(error)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["healthy"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
