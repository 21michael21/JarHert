from __future__ import annotations

import os
import sys
from pathlib import Path


hermes_home = Path(os.getenv("HERMES_HOME", "~/.hermes")).expanduser()
sys.path.insert(0, str(hermes_home))

from native_tools.telegram_text_export import (
    cleanup_expired_exports,
    telegram_export_output_directory,
    telegram_export_retention_hours,
)


output_dir = telegram_export_output_directory()
removed = cleanup_expired_exports(output_dir, retention_hours=telegram_export_retention_hours())
print(f"telegram_exports_removed={removed} directory={output_dir}")
