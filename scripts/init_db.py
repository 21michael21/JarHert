from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.config import Settings
from scripts.run_migrations import run_migrations


def main() -> int:
    settings = Settings()
    run_migrations(settings.database_url)
    print(f"DB migrated: {settings.database_url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
