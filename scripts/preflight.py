from __future__ import annotations

import shutil
import sys
import urllib.error
import urllib.request
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.config import Settings
from backend.db import init_db, make_session_factory


def _check_http(url: str, timeout: float = 3) -> tuple[bool, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.status < 500, f"HTTP {response.status}"
    except urllib.error.HTTPError as error:
        return error.code < 500, f"HTTP {error.code}"
    except Exception as error:
        return False, f"{type(error).__name__}: {error}"


def _check_telegram_bot(token: str, timeout: float = 5) -> tuple[bool, str]:
    url = f"https://api.telegram.org/bot{token}/getMe"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as error:
        return False, f"HTTP {error.code}"
    except Exception as error:
        return False, f"{type(error).__name__}: {error}"
    if not payload.get("ok"):
        return False, "telegram returned ok=false"
    result = payload.get("result") or {}
    username = result.get("username") or "unknown"
    return True, f"@{username}"


def _check_telegram_webhook(token: str, timeout: float = 5) -> tuple[bool, str]:
    url = f"https://api.telegram.org/bot{token}/getWebhookInfo"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as error:
        return False, f"HTTP {error.code}"
    except Exception as error:
        return False, f"{type(error).__name__}: {error}"
    if not payload.get("ok"):
        return False, "telegram returned ok=false"
    result = payload.get("result") or {}
    webhook_url = result.get("url") or ""
    pending = result.get("pending_update_count", 0)
    mode = "webhook=set" if webhook_url else "webhook=empty_polling_possible"
    return True, f"{mode} pending_updates={pending}"


def main() -> int:
    settings = Settings()
    failures: list[str] = []

    print("Telegram AI Brooch preflight")
    print(f"app_env={settings.app_env}")
    print(f"database_url={settings.database_url}")
    print(f"hermes_mode={settings.hermes_mode}")
    print(f"ai_reply_to_plain_text={settings.ai_reply_to_plain_text}")
    print(f"allowed_users={'set' if settings.allowed_tg_user_ids else 'empty'}")

    if settings.bot_token:
        print("bot_token=set")
        ok, detail = _check_telegram_bot(settings.bot_token)
        print(f"telegram_get_me={detail}")
        if not ok:
            failures.append("Telegram BOT_TOKEN validation failed")
        webhook_ok, webhook_detail = _check_telegram_webhook(settings.bot_token)
        print(f"telegram_webhook={webhook_detail}")
        if not webhook_ok:
            failures.append("Telegram webhook info failed")
        if detail == "@biba_book_bot":
            print("shared_token_warning=token belongs to Telegram Library bot; do not run two polling services at once")
    else:
        print("bot_token=missing")
        failures.append("BOT_TOKEN is required for real Telegram polling")

    try:
        factory = make_session_factory(settings.database_url)
        init_db(factory)
        print("db=ok")
    except Exception as error:
        print(f"db=fail {type(error).__name__}: {error}")
        failures.append("database initialization failed")

    if settings.hermes_mode == "fake":
        print("hermes=fake mode, no external runtime required")
    elif settings.hermes_mode in {"cli", "cli_router"}:
        command = (
            settings.hermes_cli_command_template.split()[0]
            if settings.hermes_mode == "cli_router"
            else settings.hermes_cli_command.split()[0]
        )
        path = shutil.which(command)
        if path:
            print(f"hermes_cli=ok {path}")
            if settings.hermes_mode == "cli_router":
                print(f"hermes_cli_models={','.join(settings.hermes_cli_models)}")
                print(f"paid_fallback={'enabled' if settings.ai_allow_paid_fallback else 'disabled'}")
        else:
            print(f"hermes_cli=missing command={command}")
            failures.append("Hermes CLI command not found")
    elif settings.hermes_mode == "openai_router":
        if settings.openai_api_key:
            print(f"openai=ok model={settings.openai_model}")
        else:
            print("openai=missing OPENAI_API_KEY")
            failures.append("OPENAI_API_KEY is required for openai_router")
        command = settings.hermes_cli_command_template.split()[0]
        path = shutil.which(command)
        if path:
            print(f"fallback_cli=ok {path}")
            print(f"fallback_models={','.join(settings.hermes_cli_models)}")
        else:
            print(f"fallback_cli=missing command={command}")
            failures.append("Fallback Hermes CLI command not found")
    elif settings.hermes_mode == "http":
        health_url = settings.hermes_api_url.rstrip("/") + "/health"
        ok, detail = _check_http(health_url, timeout=settings.hermes_timeout_seconds)
        print(f"hermes_http_health={detail}")
        if not ok:
            failures.append("Hermes HTTP health check failed")
    else:
        print(f"hermes=unsupported mode {settings.hermes_mode}")
        failures.append("unsupported HERMES_MODE")

    if failures:
        print("preflight=fail")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("preflight=ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
