from __future__ import annotations

import os
import sys
from pathlib import Path


hermes_home = Path(os.getenv("HERMES_HOME", "~/.hermes")).expanduser()
sys.path.insert(0, str(hermes_home))

from native_tools.mcp_api import NativeToolsAPI, personal_os_database_path


result = NativeToolsAPI(database_path=personal_os_database_path()).memory_consolidate()
print(f"memory consolidation status={result['status']} scopes={result['scopes']} facts={result['facts']}")
