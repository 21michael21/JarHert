"""Set a per-owner Telegram menu button for the JarHert Mini App."""

from __future__ import annotations

import argparse
import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def _owner_id() -> int:
    raw = (
        os.getenv("JARHERT_DASHBOARD_ALLOWED_TG_USER_IDS", "").strip()
        or os.getenv("TELEGRAM_ALLOWED_USERS", "").strip()
    )
    values = [item.strip() for item in raw.split(",") if item.strip()]
    if len(values) != 1 or not values[0].isdigit() or int(values[0]) <= 0:
        raise RuntimeError("Set exactly one dashboard owner in JARHERT_DASHBOARD_ALLOWED_TG_USER_IDS.")
    return int(values[0])


def configure_menu_button(*, url: str, token: str, chat_id: int) -> None:
    if not url.startswith("https://"):
        raise ValueError("Dashboard URL must use HTTPS.")
    payload = json.dumps(
        {
            "chat_id": chat_id,
            "menu_button": {"type": "web_app", "text": "Кабинет", "web_app": {"url": url}},
        }
    ).encode("utf-8")
    request = Request(
        f"https://api.telegram.org/bot{token}/setChatMenuButton",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=15) as response:  # noqa: S310 - fixed Telegram Bot API host.
            result = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError) as error:
        raise RuntimeError("Telegram menu button request failed") from error
    if not result.get("ok"):
        raise RuntimeError("Telegram rejected the Mini App menu button")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="Public HTTPS URL of the Mini App")
    args = parser.parse_args()
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required")
    configure_menu_button(url=args.url.strip(), token=token, chat_id=_owner_id())
    print("dashboard_menu_button=configured")


if __name__ == "__main__":
    main()
