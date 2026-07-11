from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from native_tools.delivery import HermesTelegramSender
from native_tools.mcp_api import NativeToolsAPI
from native_tools.operator_canary import OperatorCanaryError, run_operator_canary
from native_tools.task_calendar import TaskCalendarAdapter, TaskCalendarError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a cleanup-guaranteed JarHert integration canary.")
    parser.add_argument("--allow-external", action="store_true", help="Required: creates and removes temporary task/calendar data.")
    parser.add_argument("--chat-id", type=int, default=_owner_chat_id())
    parser.add_argument("--run-id", default=f"canary-{uuid.uuid4().hex[:12]}")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.allow_external:
        print(json.dumps({"ok": False, "error": "Pass --allow-external to run the real integration canary."}))
        return 2
    if args.chat_id <= 0:
        print(json.dumps({"ok": False, "error": "Set HERMES_OWNER_TELEGRAM_CHAT_ID or pass --chat-id."}))
        return 2
    try:
        result = run_operator_canary(
            api=NativeToolsAPI(),
            adapter=TaskCalendarAdapter.from_env(),
            sender=HermesTelegramSender(),
            chat_id=args.chat_id,
            run_id=args.run_id,
        )
    except (OperatorCanaryError, TaskCalendarError, ValueError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


def _owner_chat_id() -> int:
    raw = os.getenv("HERMES_OWNER_TELEGRAM_CHAT_ID", "").strip()
    return int(raw) if raw.isdigit() else 0


if __name__ == "__main__":
    raise SystemExit(main())
