from __future__ import annotations

import os
import sys
from pathlib import Path


hermes_home = Path(os.getenv("HERMES_HOME", "~/.hermes")).expanduser()
sys.path.insert(0, str(hermes_home))

from native_tools.cli import database_path
from native_tools.contacts import ContactStore
from native_tools.delivery import HermesTelegramSender, dispatch_due_messages


result = dispatch_due_messages(ContactStore(database_path()), HermesTelegramSender())
if result["failed"]:
    print(f"Scheduled Telegram delivery failed: {result['failed']} of {result['claimed']}")
    raise SystemExit(1)
