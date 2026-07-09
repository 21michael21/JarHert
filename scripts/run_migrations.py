from __future__ import annotations

import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.config import Settings


APP_TABLES = {
    "automation_worker_leases",
    "users",
    "memories",
    "ideas",
    "reminders",
    "agent_jobs",
    "agent_actions",
    "conversation_turns",
    "user_preferences",
    "provider_health",
    "delivery_outbox",
    "monitor_jobs",
    "monitor_runs",
    "usage_daily",
    "events",
    "messages",
}


def run_migrations(database_url: str | None = None) -> None:
    settings = Settings()
    url = database_url or settings.database_url
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url)
    cfg.attributes["database_url"] = url

    engine = create_engine(url, future=True)
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    if APP_TABLES & table_names and "alembic_version" not in table_names:
        command.stamp(cfg, "head")
    command.upgrade(cfg, "head")


def main() -> int:
    run_migrations()
    print("migrations=ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
