from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from assistant.hermes_client import HermesClientError
from assistant.limits import DailyLimitStore
from assistant.pipeline import AssistantPipeline
from assistant.types import UserContext
from gateway_bot.main import build_hermes_client


def main() -> int:
    pipeline = AssistantPipeline(
        hermes=build_hermes_client(),
        limits=DailyLimitStore(per_user_limit=5, global_limit=10),
    )
    user = UserContext(user_id=1, tg_user_id=1, is_admin=True)
    try:
        reply = pipeline.handle_text(user, "/ask ответь одним коротким предложением: Hermes adapter работает?")
    except HermesClientError as error:
        print(f"FAIL hermes_error={error}")
        return 1

    print(f"intent={reply.intent.value}")
    print(f"provider={reply.provider or 'none'}")
    print(f"model={reply.model or 'none'}")
    print(f"fallback_count={reply.fallback_count}")
    print(f"blocked_reason={reply.blocked_reason or 'none'}")
    print(f"text={reply.text}")
    return 0 if reply.blocked_reason is None else 1


if __name__ == "__main__":
    raise SystemExit(main())
