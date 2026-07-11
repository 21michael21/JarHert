from __future__ import annotations

import os
import sys
from pathlib import Path


hermes_home = Path(os.getenv("HERMES_HOME", "~/.hermes")).expanduser()
sys.path.insert(0, str(hermes_home))

from native_tools.cli import database_path
from native_tools.contacts import ContactStore
from native_tools.delivery import HermesTelegramSender, dispatch_due_messages, dispatch_due_reminders
from native_tools.personal_productivity import PersonalProductivityStore
from native_tools.personal_crm import PersonalCRMStore


sender = HermesTelegramSender()
productivity = PersonalProductivityStore(database_path())
crm = PersonalCRMStore(database_path())


def log_sent(message, _external_id) -> None:
    crm.log_interaction(
        contact=message.contact_name,
        kind="message",
        summary=message.text,
        idempotency_key=f"scheduled-message:{message.id}",
    )


result = dispatch_due_messages(ContactStore(database_path()), sender, on_sent=log_sent)
owner_chat_id = os.getenv("HERMES_OWNER_TELEGRAM_CHAT_ID", "").strip()
if not owner_chat_id:
    owner_chat_id = os.getenv("ADMIN_TG_USER_IDS", "").split(",", 1)[0].strip()
reminders = {"claimed": 0, "sent": 0, "failed": 0}
if owner_chat_id:
    reminders = dispatch_due_reminders(
        productivity,
        sender,
        chat_id=int(owner_chat_id),
    )
if result["failed"] or reminders["failed"]:
    print(
        "Scheduled Telegram delivery failed: "
        f"messages={result['failed']}/{result['claimed']}, "
        f"reminders={reminders['failed']}/{reminders['claimed']}"
    )
    raise SystemExit(1)
