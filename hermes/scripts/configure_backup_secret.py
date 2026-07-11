from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from native_tools.operations import write_backup_secret


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Configure the local encrypted-backup recovery secret.")
    parser.add_argument(
        "--destination",
        type=Path,
        default=Path("~/.config/jarhert/backup.env").expanduser(),
    )
    parser.add_argument("--replace", action="store_true", help="Replace an existing secret file after confirmation.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        first = getpass.getpass("Backup recovery passphrase (store it outside this VPS): ")
        second = getpass.getpass("Repeat passphrase: ")
        if first != second:
            raise ValueError("Passphrases do not match.")
        write_backup_secret(args.destination, passphrase=first, replace=args.replace)
    except (OSError, ValueError) as error:
        print(f"backup_secret_configured=false error={error}", file=sys.stderr)
        return 2
    print("backup_secret_configured=true")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
