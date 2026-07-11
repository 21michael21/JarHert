from __future__ import annotations

import json
import os
import sys
from pathlib import Path


hermes_home = Path(os.getenv("HERMES_HOME", "~/.hermes")).expanduser()
sys.path.insert(0, str(hermes_home))

from native_tools.mcp_api import NativeToolsAPI, personal_os_database_path


digest = NativeToolsAPI(database_path=personal_os_database_path()).monitor_digest()
if digest["items"]:
    print(json.dumps(digest, ensure_ascii=False, separators=(",", ":")))
