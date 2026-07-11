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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check the Hermes gateway without touching healthy processes.")
    parser.add_argument("--unit", default="hermes-gateway-jarhert.service")
    parser.add_argument("--path", type=Path, default=Path.home())
    parser.add_argument("--min-disk-free-gib", type=float, default=5.0)
    parser.add_argument("--max-memory-percent", type=float, default=90.0)
    parser.add_argument("--restart-inactive", action="store_true")
    parser.add_argument("--fail-on-zombies", action="store_true")
    return parser


def run(args: argparse.Namespace) -> dict[str, object]:
    active = _systemctl("is-active", args.unit).stdout.strip() == "active"
    restarted = False
    if not active and args.restart_inactive:
        _systemctl("restart", args.unit, check=True)
        active = _systemctl("is-active", args.unit).stdout.strip() == "active"
        restarted = active
    main_pid = _main_pid(args.unit) if active else None
    disk = shutil.disk_usage(args.path)
    disk_free_gib = round(disk.free / (1024**3), 2)
    total_memory, available_memory = parse_meminfo(Path("/proc/meminfo").read_text(encoding="utf-8"))
    memory_used_percent = round(100 * (1 - available_memory / total_memory), 1)
    process_table = _command(["ps", "-eo", "stat=,ppid=,pid="]).stdout
    zombies = zombie_processes(process_table, parent_pid=main_pid)
    healthy = (
        active
        and main_pid is not None
        and disk_free_gib >= args.min_disk_free_gib
        and memory_used_percent <= args.max_memory_percent
        and (not args.fail_on_zombies or not zombies)
    )
    return {
        "healthy": healthy,
        "unit": args.unit,
        "active": active,
        "main_pid": main_pid,
        "restarted": restarted,
        "disk_free_gib": disk_free_gib,
        "memory_used_percent": memory_used_percent,
        "zombie_children": zombies,
    }


def _systemctl(*arguments: str, check: bool = False) -> subprocess.CompletedProcess[str]:
    return _command(["systemctl", "--user", *arguments], check=check)


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
