from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from assistant.communication_style import load_communication_style
from assistant.training_feedback_export import build_approved_feedback_records
from backend.config import Settings
from backend.db import make_session_factory
from backend.migrations import require_current_schema
from backend.stores import SqlTrainingFeedbackStore, UserStore


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export only explicitly approved, redacted Telegram feedback pairs."
    )
    parser.add_argument("--tg-user-id", type=int, required=True)
    parser.add_argument("--output", type=Path, default=Path("data/training/approved_feedback.jsonl"))
    parser.add_argument("--limit", type=int, default=400)
    parser.add_argument("--confirm-consent", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.limit <= 400:
        raise SystemExit("--limit must be between 1 and 400")
    if not args.dry_run and not args.confirm_consent:
        raise SystemExit("Refusing export without --confirm-consent")

    settings = Settings()
    require_current_schema(settings.database_url)
    factory = make_session_factory(settings.database_url)
    user = UserStore(factory).get_or_create(args.tg_user_id)
    examples = SqlTrainingFeedbackStore(factory).list_approved(user.id, limit=args.limit)
    print(f"approved_feedback_export selected={len(examples)} target=250..400 dry_run={args.dry_run}")
    if args.dry_run:
        return 0

    system_prompt = load_communication_style(enabled=True).render("concise")
    records = build_approved_feedback_records(system_prompt=system_prompt, examples=examples)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(f"approved_feedback_export written={len(records)} output={args.output}")
    if len(records) < 250:
        print("approved_feedback_export note=collect_more_explicit_feedback_before_fine_tune")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
