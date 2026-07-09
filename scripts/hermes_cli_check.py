from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from assistant.hermes_client import HermesCliClient, HermesClientError
from assistant.types import HermesRequest, Intent, UserContext
from backend.config import Settings


def main() -> int:
    parser = argparse.ArgumentParser(description="Check real Hermes CLI integration without Telegram polling.")
    parser.add_argument("--timeout", type=float, default=None, help="Override Hermes CLI timeout seconds.")
    args = parser.parse_args()

    settings = Settings()
    command = settings.hermes_cli_command
    binary = command.split()[0] if command.split() else ""
    if not binary or shutil.which(binary) is None:
        print(f"hermes_cli=missing command={binary or '<empty>'}")
        return 1

    timeout = args.timeout or settings.hermes_timeout_seconds
    client = HermesCliClient(command, timeout_seconds=timeout)
    request = HermesRequest(
        user=UserContext(user_id=1, tg_user_id=1, is_admin=True),
        prompt="Ответь одним коротким русским предложением: Hermes CLI интеграция работает?",
        intent=Intent.ASK,
    )
    try:
        response = client.ask(request)
    except HermesClientError as error:
        print(f"hermes_cli=fail {error}")
        print("hint=проверь, что в ~/.hermes/.env настроен хотя бы один LLM provider key")
        return 1

    print("hermes_cli=ok")
    print(f"provider={response.provider}")
    print(f"model={response.model}")
    print(f"latency_ms={response.latency_ms}")
    print(f"text={response.text[:500]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
