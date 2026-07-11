from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from native_tools.operations import (
    BackupRetention,
    create_encrypted_backup,
    restore_encrypted_backup,
    verify_restored_profile,
)


def _profile_home(value: str | None) -> Path:
    return Path(value or os.getenv("HERMES_HOME", Path(__file__).resolve().parents[1])).expanduser()


def _passphrase(name: str) -> str:
    value = os.getenv(name, "")
    if not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create and verify encrypted Hermes profile backups.")
    parser.add_argument("--profile-home")
    parser.add_argument("--backup-dir", type=Path, default=Path("~/.hermes/backups/jarhert").expanduser())
    parser.add_argument("--passphrase-env", default="HERMES_BACKUP_PASSPHRASE")
    parser.add_argument("--keep-daily", type=int, default=7)
    parser.add_argument("--keep-weekly", type=int, default=4)
    parser.add_argument("--keep-monthly", type=int, default=3)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("backup")
    restore = commands.add_parser("restore")
    restore.add_argument("--archive", type=Path, required=True)
    restore.add_argument("--destination", type=Path, required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("--archive", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = _run(args)
    except (OSError, RuntimeError, ValueError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False))
        return 2
    print(json.dumps({"ok": True, **result}, ensure_ascii=False))
    return 0


def _run(args: argparse.Namespace) -> dict[str, object]:
    profile_home = _profile_home(args.profile_home)
    passphrase = _passphrase(args.passphrase_env)
    if args.command == "backup":
        result = create_encrypted_backup(
            profile_home=profile_home,
            backup_dir=args.backup_dir,
            passphrase=passphrase,
            retention=BackupRetention(args.keep_daily, args.keep_weekly, args.keep_monthly),
        )
        return {"archive": str(result.archive), "removed": [str(path) for path in result.removed]}
    if args.command == "restore":
        destination = restore_encrypted_backup(
            archive=args.archive,
            destination=args.destination,
            passphrase=passphrase,
        )
        return {"destination": str(destination), "integrity": verify_restored_profile(destination)}
    with tempfile.TemporaryDirectory(prefix="jarhert-backup-verify-") as temporary:
        destination = Path(temporary) / "restored"
        restore_encrypted_backup(archive=args.archive, destination=destination, passphrase=passphrase)
        integrity = verify_restored_profile(destination)
    return {"verified": True, "integrity": integrity}


if __name__ == "__main__":
    raise SystemExit(main())
