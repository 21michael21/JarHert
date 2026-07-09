from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from assistant.hermes_client import FakeHermesClient
from assistant.limits import DailyLimitStore
from assistant.pipeline import AssistantPipeline
from assistant.quality_gates import check_output
from assistant.natural_router import route_natural_text
from assistant.types import UserContext


DEFAULT_FIXTURE_DIR = PROJECT_ROOT / "tests" / "golden_dialogs"
DEFAULT_REPORT_DIR = PROJECT_ROOT / "reports" / "golden_eval"


@dataclass(frozen=True)
class GoldenResult:
    id: str
    ok: bool
    errors: list[str]


def evaluate_case(case: dict) -> GoldenResult:
    errors: list[str] = []
    text = str(case["text"])

    if "expected_actions" in case:
        route = route_natural_text(text)
        expected_actions = list(case.get("expected_actions") or [])
        if [action.type.value for action in route.actions] != [item["type"] for item in expected_actions]:
            errors.append(
                f"actions mismatch: got={[action.type.value for action in route.actions]} "
                f"expected={[item['type'] for item in expected_actions]}"
            )
        for index, expected in enumerate(expected_actions):
            if index >= len(route.actions):
                break
            expected_payload = expected.get("payload_contains") or {}
            for key, value in expected_payload.items():
                actual = route.actions[index].payload.get(key)
                if actual != value:
                    errors.append(f"payload[{index}].{key}: got={actual!r} expected={value!r}")
        if "expected_fallback_to_ai" in case and route.fallback_to_ai != bool(case["expected_fallback_to_ai"]):
            errors.append(f"fallback_to_ai: got={route.fallback_to_ai!r} expected={case['expected_fallback_to_ai']!r}")

    if "expected_reply_contains" in case:
        pipeline = AssistantPipeline(FakeHermesClient(), DailyLimitStore())
        reply = pipeline.handle_text(UserContext(user_id=1, tg_user_id=1001), text)
        output_gate = check_output(reply.text, max_chars=int(case.get("max_reply_chars", 1200)))
        if not output_gate.ok:
            errors.append(f"reply quality gate failed: {output_gate.reason}")
        for needle in case.get("expected_reply_contains") or []:
            if needle not in reply.text:
                errors.append(f"reply missing {needle!r}")

    return GoldenResult(id=str(case["id"]), ok=not errors, errors=errors)


def evaluate_files(paths: list[Path]) -> list[GoldenResult]:
    cases: list[dict] = []
    for path in paths:
        cases.extend(json.loads(path.read_text(encoding="utf-8")))
    return [evaluate_case(case) for case in cases]


def main() -> int:
    parser = argparse.ArgumentParser(description="Run golden natural UX and response quality evals.")
    parser.add_argument("paths", nargs="*", type=Path, help="Optional JSON fixture files.")
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    args = parser.parse_args()

    paths = args.paths or sorted(DEFAULT_FIXTURE_DIR.glob("*.json"))
    results = evaluate_files(paths)
    passed = sum(1 for item in results if item.ok)
    failed = len(results) - passed
    payload = {
        "ok": failed == 0,
        "passed": passed,
        "failed": failed,
        "results": [asdict(item) for item in results],
    }

    args.report_dir.mkdir(parents=True, exist_ok=True)
    report_path = args.report_dir / f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"golden_eval passed={passed} failed={failed} report={report_path}")
    for result in results:
        if not result.ok:
            print(f"- {result.id}: {'; '.join(result.errors)}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
