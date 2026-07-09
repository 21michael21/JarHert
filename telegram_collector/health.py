from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


@dataclass
class CollectorHealth:
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    connected: bool = False
    tracked_chats: int = 0
    written_count: int = 0
    last_message_at: datetime | None = None
    last_error: str | None = None

    def as_dict(self) -> dict[str, str | int | bool | None]:
        return {
            "status": "ok" if self.connected and not self.last_error else "degraded",
            "service": "telegram_collector",
            "connected": self.connected,
            "tracked_chats": self.tracked_chats,
            "written_count": self.written_count,
            "started_at": self.started_at.isoformat(),
            "last_message_at": self.last_message_at.isoformat() if self.last_message_at else None,
            "last_error": self.last_error,
        }


def start_health_server(host: str, port: int, state: CollectorHealth) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path not in {"/health", "/"}:
                self.send_response(404)
                self.end_headers()
                return
            body = json.dumps(state.as_dict(), ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args) -> None:  # noqa: A002
            return

    server = ThreadingHTTPServer((host, port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server
