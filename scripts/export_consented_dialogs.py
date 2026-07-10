from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import select


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from assistant.communication_style import load_communication_style
from assistant.training_data import build_consented_record
from backend.config import Settings
from backend.db import make_session_factory
from backend.migrations import require_current_schema
from backend.models import ConversationTurnRecord, User


def main() -> int:
    parser = argparse.ArgumentParser(description="Export explicitly consented dialogue turns for local curation.")
    parser.add_argument("--tg-user-id", type=int, required=True)
    parser.add_argument("--turn-id", type=int, action="append", required=True)
    parser.add_argument("--output", type=Path, default=Path("data/training/consented_dialogs.jsonl"))
    parser.add_argument("--confirm-consent", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if not args.dry_run and not args.confirm_consent:
        raise SystemExit("Refusing export without --confirm-consent")

    database_url = Settings().database_url
    require_current_schema(database_url)
    factory = make_session_factory(database_url)
    requested_ids = sorted(set(args.turn_id))
    with factory() as db:
        user = db.scalar(select(User).where(User.tg_user_id == args.tg_user_id))
        if user is None:
            raise SystemExit("Telegram user not found")
        turns = db.scalars(
            select(ConversationTurnRecord)
            .where(
                ConversationTurnRecord.user_id == user.id,
                ConversationTurnRecord.id.in_(requested_ids),
            )
            .order_by(ConversationTurnRecord.id.asc())
        ).all()
    found_ids = {turn.id for turn in turns}
    missing = [turn_id for turn_id in requested_ids if turn_id not in found_ids]
    if missing:
        raise SystemExit(f"Owned conversation turns not found: {','.join(map(str, missing))}")

    print(f"consented_dialogue_export selected={len(turns)} dry_run={args.dry_run}")
    if args.dry_run:
        return 0

    system_prompt = load_communication_style(enabled=True).render("concise")
    records = [
        build_consented_record(
            system_prompt=system_prompt,
            user_text=turn.user_text,
            assistant_text=turn.assistant_text,
            source_turn_id=turn.id,
        )
        for turn in turns
    ]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(f"consented_dialogue_export written={len(records)} output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
