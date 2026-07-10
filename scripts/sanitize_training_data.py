from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from assistant.training_data import redact_dataset_rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a redacted local-only JSONL copy for training review.")
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not args.source.is_file():
        raise SystemExit(f"Dataset not found: {args.source}")

    rows: list[dict] = []
    with args.source.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as error:
                raise SystemExit(f"Invalid JSON at line {line_number}") from error
            if not isinstance(row, dict):
                raise SystemExit(f"Expected object at line {line_number}")
            rows.append(row)

    output = args.output or PROJECT_ROOT / "data" / "training" / f"{args.source.stem}.sanitized.jsonl"
    output.parent.mkdir(parents=True, exist_ok=True)
    sanitized, findings = redact_dataset_rows(rows)
    with output.open("w", encoding="utf-8") as stream:
        for row in sanitized:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(
        f"training_data_sanitized rows={len(sanitized)} findings={sum(findings.values())} "
        f"output={output} created_at={datetime.now(timezone.utc).isoformat()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
