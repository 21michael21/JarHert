"""Run the local-only JarHert Telegram Mini App service."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import uvicorn

# systemd invokes this file directly, so Python otherwise puts only scripts/
# on sys.path and cannot resolve the sibling native_tools package.
PROFILE_ROOT = Path(__file__).resolve().parents[1]
if str(PROFILE_ROOT) not in sys.path:
    sys.path.insert(0, str(PROFILE_ROOT))

from native_tools.dashboard import create_app


def main() -> None:
    host = os.getenv("JARHERT_DASHBOARD_HOST", "127.0.0.1")
    port = int(os.getenv("JARHERT_DASHBOARD_PORT", "8788"))
    uvicorn.run(create_app(), host=host, port=port, proxy_headers=True, forwarded_allow_ips="127.0.0.1")


if __name__ == "__main__":
    main()
