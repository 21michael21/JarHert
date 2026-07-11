from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


hermes_home = Path(os.getenv("HERMES_HOME", "~/.hermes")).expanduser()
sys.path.insert(0, str(hermes_home))

from native_tools.delivery import HermesTelegramSender
from native_tools.mcp_api import NativeToolsAPI, personal_os_database_path
from native_tools.personal_rhythms import PersonalRhythmStore, dispatch_personal_summary


parser = argparse.ArgumentParser()
parser.add_argument("--kind", choices=["daily", "weekly"], required=True)
parser.add_argument("--timezone", default=os.getenv("HERMES_TIMEZONE", "Europe/Moscow"))
args = parser.parse_args()

chat_id = os.getenv("HERMES_OWNER_TELEGRAM_CHAT_ID", "").strip()
if not chat_id:
    chat_id = os.getenv("ADMIN_TG_USER_IDS", "").split(",", 1)[0].strip()
if not chat_id:
    raise SystemExit("HERMES_OWNER_TELEGRAM_CHAT_ID is required")

now = datetime.now(ZoneInfo(args.timezone))
period_key = (
    now.date().isoformat()
    if args.kind == "daily"
    else f"{now.isocalendar().year}-W{now.isocalendar().week:02d}"
)
api = NativeToolsAPI(database_path=personal_os_database_path())


def build_text() -> str:
    if args.kind == "daily":
        return str(api.personal_daily_brief(now=now.isoformat(), timezone_name=args.timezone)["text"])
    return str(api.personal_weekly_review(now=now.isoformat(), timezone_name=args.timezone)["text"])


dispatch_personal_summary(
    PersonalRhythmStore(personal_os_database_path()),
    build_text,
    HermesTelegramSender(),
    chat_id=int(chat_id),
    summary_type=args.kind,
    period_key=period_key,
)
