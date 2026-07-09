from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path


def load_openrouter_key() -> str:
    for path in (Path(".env"), Path.home() / ".hermes" / ".env"):
        if not path.exists():
            continue
        for line in path.read_text().splitlines():
            stripped = line.strip()
            if stripped.startswith("OPENROUTER_API_KEY="):
                return stripped.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def main() -> int:
    key = load_openrouter_key()
    if not key:
        print("openrouter_key=missing")
        return 1

    request = urllib.request.Request(
        "https://openrouter.ai/api/v1/auth/key",
        headers={
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
            "User-Agent": "telegram-ai-brooch/0.1",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", "replace")
        if key:
            body = body.replace(key, "***secret***")
        print(f"openrouter_key=fail HTTP {error.code}")
        print(f"body={body[:500]}")
        return 1
    except Exception as error:
        print(f"openrouter_key=fail {type(error).__name__}: {error}")
        return 1

    data = payload.get("data", payload)
    label = data.get("label") or data.get("name") or "unknown"
    usage = data.get("usage") or data.get("usage_limit") or "unknown"
    print("openrouter_key=ok")
    print(f"label={label}")
    print(f"usage={usage}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
