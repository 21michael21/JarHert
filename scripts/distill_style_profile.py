from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from assistant.style_distillation import distill_style_profile, extract_assistant_messages


def load_rows(path: Path) -> list[dict]:
    rows: list[dict] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSONL at line {line_number}") from exc
        if not isinstance(row, dict):
            raise ValueError(f"JSONL row {line_number} must be an object")
        rows.append(row)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Distill a private redacted post corpus into a short local JarHert style profile."
    )
    parser.add_argument("source", type=Path, help="Private redacted JSONL with ChatML messages")
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "data" / "training" / "distilled_communication_style.md",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=PROJECT_ROOT / "reports" / "style_distillation" / "latest.json",
    )
    parser.add_argument("--max-response-chars", type=int, default=420)
    args = parser.parse_args()

    assistant_messages = extract_assistant_messages(load_rows(args.source))
    profile = distill_style_profile(assistant_messages, max_response_chars=args.max_response_chars)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        f"<!-- jarhert-style max_response_chars={args.max_response_chars} -->\n{profile.prompt}\n",
        encoding="utf-8",
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(asdict(profile), ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        "style_profile_distilled "
        f"messages={profile.source_messages} long_messages={profile.long_message_count} "
        f"effective_weight={profile.effective_weight} version={profile.version} output={args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
