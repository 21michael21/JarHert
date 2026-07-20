#!/usr/bin/env python3
"""Push locally collected LLM rate limits to the JarHert dashboard on the VPS.

Запускается launchd-агентом com.jarhert.limits-push каждые 5 минут на Mac,
где установлены codex и caut. VPS до Mac по SSH не достучится (NAT), поэтому
Mac сам пушит снапшот на POST /api/limits/ingest.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "hermes"))

from native_tools.dashboard import _collect_limits  # noqa: E402

ENV_FILE = Path.home() / ".config" / "jarhert" / "limits-push.env"
DEFAULT_INGEST_URL = "https://89.124.124.212.sslip.io/api/limits/ingest"


def _load_env_file(path: Path) -> None:
    """Подхватить KEY=VALUE строки, не перетирая уже выставленные переменные."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def main() -> int:
    _load_env_file(ENV_FILE)
    token = os.getenv("JARHERT_LIMITS_INGEST_TOKEN", "").strip()
    if not token:
        print(
            f"JARHERT_LIMITS_INGEST_TOKEN не задан ни в env, ни в {ENV_FILE} "
            "(формат: JARHERT_LIMITS_INGEST_TOKEN=<token>, chmod 600)",
            file=sys.stderr,
        )
        return 2
    url = os.getenv("JARHERT_LIMITS_INGEST_URL", "").strip() or DEFAULT_INGEST_URL
    payload = _collect_limits()
    if not payload.get("available"):
        detail = payload.get("detail") or payload.get("reason") or "unknown"
        print(f"локальные источники лимитов недоступны, snapshot не отправлен: {detail}", file=sys.stderr)
        return 1
    providers = payload.get("providers") or []
    body = json.dumps(
        {
            "providers": providers,
            "errors": payload.get("errors") or [],
            "generatedAt": payload.get("generatedAt"),
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            response.read()
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")[:200]
        print(f"ingest вернул HTTP {error.code}: {detail}", file=sys.stderr)
        return 1
    except urllib.error.URLError as error:
        print(f"не удалось отправить snapshot: {error.reason}", file=sys.stderr)
        return 1
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"{stamp} pushed {len(providers)} providers to {url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
