from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.config import Settings
from backend.db import init_db, make_session_factory


def main() -> int:
    settings = Settings()
    factory = make_session_factory(settings.database_url)
    init_db(factory)
    print(f"DB initialized: {settings.database_url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

