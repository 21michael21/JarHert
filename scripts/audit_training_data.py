from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from assistant.training_data import audit_dataset_rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit a local JSONL training dataset without printing content.")
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--strict", action="store_true", help="Fail on invalid rows or privacy findings.")
    args = parser.parse_args()
    if not args.source.is_file():
        raise SystemExit(f"Dataset not found: {args.source}")

    rows = []
    with args.source.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as error:
                raise SystemExit(f"Invalid JSON at line {line_number}") from error
            rows.append(value if isinstance(value, dict) else {})

    report = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_name": args.source.name,
        **audit_dataset_rows(rows),
    }
    output = args.output or args.source.with_name(f"{args.source.stem}.privacy-audit.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"training_data_audit rows={report['rows']} dialogue_rows={report['dialogue_rows']} "
        f"invalid_rows={report['invalid_rows']} findings={sum(report['privacy_findings'].values())} "
        f"report={output}"
    )
    if args.strict and (report["invalid_rows"] or report["privacy_findings"]):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
