from __future__ import annotations

import sys
import time
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import gateway_bot.main as gateway_main
from backend.config import Settings
from backend.db import init_db, make_session_factory


def main() -> int:
    settings = Settings()
    factory = make_session_factory(settings.database_url)
    init_db(factory)

    gateway_main._gateway_service = None
    tg_user_id = int(time.time())
    print(f"tg_user_id={tg_user_id}")
    print(gateway_main.handle_local_text(tg_user_id, "/status"))
    print(gateway_main.handle_local_text(tg_user_id, "/remember smoke память"))
    print(gateway_main.handle_local_text(tg_user_id, "/memories"))
    created_reminder = gateway_main.handle_local_text(tg_user_id, "/remind 2026-07-09 09:30 smoke проверка")
    print(created_reminder)
    print(gateway_main.handle_local_text(tg_user_id, "/reminders"))
    match = re.search(r"#(\d+)", created_reminder)
    if match:
        print(gateway_main.handle_local_text(tg_user_id, f"/cancel_reminder {match.group(1)}"))
    print(gateway_main.handle_local_text(tg_user_id, "/ask ответь коротко: smoke ok?"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
