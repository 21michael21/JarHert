from __future__ import annotations

import argparse
import sys
from pathlib import Path


DEFAULT_SOURCE = Path("/Users/mihailkulibaba/Documents/telegram-library/.env")
DEFAULT_TARGET = Path(__file__).resolve().parents[1] / ".env"
DEFAULT_KEYS = ("BOT_TOKEN",)


def _read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        raise FileNotFoundError(path)
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def _write_env(path: Path, updates: dict[str, str]) -> None:
    lines = path.read_text().splitlines() if path.exists() else []
    seen: set[str] = set()
    new_lines: list[str] = []
    for raw_line in lines:
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            new_lines.append(raw_line)
            continue
        key, _value = stripped.split("=", 1)
        key = key.strip()
        if key in updates:
            new_lines.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            new_lines.append(raw_line)
    for key, value in updates.items():
        if key not in seen:
            new_lines.append(f"{key}={value}")
    path.write_text("\n".join(new_lines).rstrip() + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Copy selected env keys from Telegram Library without printing secrets.")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    parser.add_argument("--key", action="append", dest="keys", default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    keys = tuple(args.keys or DEFAULT_KEYS)
    source_values = _read_env(args.source)
    updates = {key: source_values[key] for key in keys if source_values.get(key)}
    missing = [key for key in keys if not source_values.get(key)]

    print(f"source={args.source}")
    print(f"target={args.target}")
    print("copy_keys=" + ",".join(sorted(updates)))
    if missing:
        print("missing_keys=" + ",".join(sorted(missing)))
    if args.dry_run:
        print("dry_run=true")
        return 0 if updates else 1

    if not updates:
        print("import=fail no keys copied")
        return 1
    _write_env(args.target, updates)
    print("import=ok values hidden")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

