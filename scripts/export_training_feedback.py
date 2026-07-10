from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from assistant.communication_style import load_communication_style
from assistant.training_feedback import TrainingExampleType
from assistant.training_feedback_export import (
    build_approved_feedback_records,
    build_preference_records,
    split_approved_feedback_records,
    training_feedback_progress,
)
from backend.config import Settings
from backend.db import make_session_factory
from backend.migrations import require_current_schema
from backend.stores import SqlTrainingFeedbackStore, UserStore


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export only explicitly approved, redacted Telegram feedback pairs."
    )
    parser.add_argument("--tg-user-id", type=int, required=True)
    parser.add_argument("--output", type=Path, help="Optional combined JSONL for compatibility with older workflows.")
    parser.add_argument("--output-dir", type=Path, default=Path("data/training/feedback"))
    parser.add_argument("--limit", type=int, default=400)
    parser.add_argument("--confirm-consent", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--require-targets", action="store_true")
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
    progress = training_feedback_progress(examples)
    print(
        "approved_feedback_export "
        f"selected={len(examples)} target=340 dry_run={args.dry_run} "
        f"target_ready={progress['target_ready']}"
    )
    print(json.dumps(progress, ensure_ascii=False, sort_keys=True))
    if args.dry_run:
        return 0 if not args.require_targets or progress["target_ready"] else 2

    system_prompt = load_communication_style(enabled=True).render("concise")
    records = build_approved_feedback_records(system_prompt=system_prompt, examples=examples)
    groups = split_approved_feedback_records(system_prompt=system_prompt, examples=examples)
    preferences = build_preference_records(examples)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for example_type in TrainingExampleType:
        _write_jsonl(args.output_dir / f"{example_type.value}.jsonl", groups[example_type])
    _write_jsonl(args.output_dir / "preference_pairs.jsonl", preferences)
    manifest_path = args.output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(progress, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.output is not None:
        _write_jsonl(args.output, records)
    print(f"approved_feedback_export written={len(records)} output_dir={args.output_dir}")
    if not progress["target_ready"]:
        print("approved_feedback_export note=collect_more_explicit_feedback_before_fine_tune")
    return 0 if not args.require_targets or progress["target_ready"] else 2


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
