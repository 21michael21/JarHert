from __future__ import annotations

import json
import os
import sys
from pathlib import Path


hermes_home = Path(os.getenv("HERMES_HOME", "~/.hermes")).expanduser()
sys.path.insert(0, str(hermes_home))

from native_tools.cli import database_path
from native_tools.events import EventStore
from native_tools.monitors import MonitorRegistry, MonitorRunner


def main() -> int:
    path = database_path()
    changes = MonitorRunner(MonitorRegistry(path), EventStore(path)).run_once(
        daily_emit_limit=int(os.getenv("MONITOR_DAILY_LLM_BUDGET", "10")),
    )
    if changes:
        print(json.dumps({"changes": changes}, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
