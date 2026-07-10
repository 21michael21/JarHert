from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from assistant.style_quality import assess_communication_style


DEFAULT_FIXTURE = PROJECT_ROOT / "tests" / "style_dialogs.json"
DEFAULT_REPORT_DIR = PROJECT_ROOT / "reports" / "style_eval"


@dataclass(frozen=True)
class StyleEvalResult:
    id: str
    ok: bool
    expected_ok: bool
    actual_ok: bool
    score: int
    issues: tuple[str, ...]


def evaluate_cases(path: Path) -> list[StyleEvalResult]:
    cases = json.loads(path.read_text(encoding="utf-8"))
    results = []
    for case in cases:
        assessment = assess_communication_style(str(case["text"]))
        expected = bool(case["expected_ok"])
        results.append(
            StyleEvalResult(
                id=str(case["id"]),
                ok=assessment.ok == expected,
                expected_ok=expected,
                actual_ok=assessment.ok,
                score=assessment.score,
                issues=assessment.issues,
            )
        )
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate JarHert Russian communication style rules.")
    parser.add_argument("fixture", nargs="?", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    args = parser.parse_args()
    results = evaluate_cases(args.fixture)
    failed = [result for result in results if not result.ok]
    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "ok": not failed,
        "passed": len(results) - len(failed),
        "failed": len(failed),
        "results": [asdict(result) for result in results],
    }
    args.report_dir.mkdir(parents=True, exist_ok=True)
    report_path = args.report_dir / f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"style_eval passed={payload['passed']} failed={payload['failed']} report={report_path}")
    for result in failed:
        print(f"- {result.id}: expected={result.expected_ok} actual={result.actual_ok} score={result.score}")
    return int(bool(failed))


if __name__ == "__main__":
    raise SystemExit(main())
