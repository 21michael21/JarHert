from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REQUIRED_GATES = (
    "clean_clone",
    "migrations",
    "tests",
    "golden_eval",
    "provider_benchmark",
    "security_scan",
    "concurrency",
    "load",
    "kill_worker_recovery",
    "backup_restore",
    "live_telegram_e2e",
)

CRITERIA = {
    "architecture": ("clean_clone", "migrations", "concurrency"),
    "code": ("clean_clone", "tests", "security_scan"),
    "tests": ("tests", "golden_eval", "concurrency", "kill_worker_recovery", "backup_restore"),
    "ux": ("golden_eval", "live_telegram_e2e"),
    "quality": ("golden_eval", "provider_benchmark", "live_telegram_e2e"),
    "speed": ("load", "provider_benchmark", "concurrency"),
    "reliability": ("migrations", "concurrency", "kill_worker_recovery", "backup_restore", "live_telegram_e2e"),
    "security": ("security_scan", "concurrency", "live_telegram_e2e"),
    "functionality": ("tests", "golden_eval", "live_telegram_e2e"),
    "cost": ("provider_benchmark",),
    "operations": ("clean_clone", "migrations", "backup_restore", "live_telegram_e2e"),
}


def build_scorecard(results: list[dict[str, Any]], *, commit: str) -> dict[str, Any]:
    supplied = {str(item.get("name")): dict(item) for item in results if item.get("name")}
    gates: dict[str, dict[str, Any]] = {}
    for name in REQUIRED_GATES:
        item = supplied.get(name, {})
        status = str(item.get("status") or "skipped")
        if status not in {"passed", "failed", "skipped"}:
            status = "failed"
        gates[name] = {
            "status": status,
            "duration_ms": int(item.get("duration_ms") or 0),
            "log": str(item.get("log") or ""),
            "detail": str(item.get("detail") or ("gate result missing" if not item else "")),
        }

    summary = {
        status: sum(gate["status"] == status for gate in gates.values())
        for status in ("passed", "failed", "skipped")
    }
    eligible = summary == {"passed": len(REQUIRED_GATES), "failed": 0, "skipped": 0}
    criteria: dict[str, dict[str, Any]] = {}
    for criterion, evidence in CRITERIA.items():
        passed = sum(gates[name]["status"] == "passed" for name in evidence)
        score = round(7.5 + 2.0 * passed / len(evidence), 2)
        criteria[criterion] = {
            "score": score,
            "evidence": list(evidence),
            "passed": passed,
            "required": len(evidence),
        }

    overall = round(sum(item["score"] for item in criteria.values()) / len(criteria), 2)
    if not eligible:
        overall = min(overall, 9.4)
    return {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "commit": commit or "unknown",
        "eligible_for_95": eligible,
        "overall_score": overall,
        "scoring_note": (
            "9.5 is release-evidence confidence, not a substitute for an architectural review. "
            "Every mandatory gate must pass in the same run."
        ),
        "required_summary": summary,
        "gates": gates,
        "criteria": criteria,
    }


def read_results(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    results = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"Invalid JSONL at line {line_number}") from error
        if not isinstance(item, dict):
            raise ValueError(f"Expected object at line {line_number}")
        results.append(item)
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the fail-closed JarHert 9.5 release scorecard.")
    parser.add_argument("--results-jsonl", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--commit", default="unknown")
    args = parser.parse_args(argv)

    scorecard = build_scorecard(read_results(args.results_jsonl), commit=args.commit)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(scorecard, ensure_ascii=False, indent=2), encoding="utf-8")
    print("criterion\tscore\tpassed")
    for name, item in scorecard["criteria"].items():
        print(f"{name}\t{item['score']:.2f}\t{item['passed']}/{item['required']}")
    summary = scorecard["required_summary"]
    print(
        f"overall={scorecard['overall_score']:.2f} eligible_for_95={str(scorecard['eligible_for_95']).lower()} "
        f"passed={summary['passed']} failed={summary['failed']} skipped={summary['skipped']} "
        f"report={args.output}"
    )
    return 0 if scorecard["eligible_for_95"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
