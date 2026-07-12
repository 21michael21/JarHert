"""Run the local-only JarHert Telegram Mini App service."""

from __future__ import annotations

import os

import uvicorn

from native_tools.dashboard import create_app


def main() -> None:
    host = os.getenv("JARHERT_DASHBOARD_HOST", "127.0.0.1")
    port = int(os.getenv("JARHERT_DASHBOARD_PORT", "8788"))
    uvicorn.run(create_app(), host=host, port=port, proxy_headers=True, forwarded_allow_ips="127.0.0.1")


if __name__ == "__main__":
    main()
