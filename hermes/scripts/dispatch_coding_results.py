from __future__ import annotations

import os
import sys
from pathlib import Path


hermes_home = Path(os.getenv("HERMES_HOME", "~/.hermes")).expanduser()
sys.path.insert(0, str(hermes_home))

from native_tools.coding_jobs import NativeCodingJobStore, dispatch_completed_coding_jobs
from native_tools.delivery import HermesTelegramSender
from native_tools.mcp_api import personal_os_database_path


result = dispatch_completed_coding_jobs(
    NativeCodingJobStore(personal_os_database_path()),
    HermesTelegramSender(),
)
if result["failed"]:
    raise SystemExit(1)
