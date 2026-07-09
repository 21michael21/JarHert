from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.config import Settings
from backend.migrations import run_migrations as _run_migrations


def run_migrations(database_url: str | None = None) -> None:
    settings = Settings()
    url = database_url or settings.database_url
    _run_migrations(url)


def main() -> int:
    run_migrations()
    print("migrations=ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
