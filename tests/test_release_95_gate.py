from __future__ import annotations

import json
import subprocess
from pathlib import Path

from scripts.backup_restore_check import run_backup_restore_check
from scripts.local_load_test import run_load_test
from scripts.release_scorecard import CRITERIA, REQUIRED_GATES, build_scorecard, main
from scripts.security_scan import find_secret_locations


def gate_results(*, override: dict[str, str] | None = None) -> list[dict]:
    statuses = {name: "passed" for name in REQUIRED_GATES}
    statuses.update(override or {})
    return [
        {"name": name, "status": status, "duration_ms": 10, "log": f"{name}.log"}
        for name, status in statuses.items()
    ]


def test_complete_release_evidence_is_eligible_for_95() -> None:
    scorecard = build_scorecard(gate_results(), commit="abc123")

    assert scorecard["eligible_for_95"] is True
    assert scorecard["overall_score"] == 9.5
    assert scorecard["required_summary"] == {"passed": 11, "failed": 0, "skipped": 0}
    assert set(scorecard["criteria"]) == set(CRITERIA)


def test_skipped_or_missing_gate_caps_score_below_95() -> None:
    results = gate_results(override={"live_telegram_e2e": "skipped"})
    results = [item for item in results if item["name"] != "backup_restore"]

    scorecard = build_scorecard(results, commit="abc123")

    assert scorecard["eligible_for_95"] is False
    assert scorecard["overall_score"] < 9.5
    assert scorecard["required_summary"]["skipped"] == 2
    assert scorecard["gates"]["backup_restore"]["status"] == "skipped"


def test_cli_exits_nonzero_and_writes_scorecard_when_required_gate_fails(tmp_path) -> None:
    results_path = tmp_path / "results.jsonl"
    output_path = tmp_path / "scorecard.json"
    results = gate_results(override={"security_scan": "failed"})
    results_path.write_text(
        "".join(json.dumps(item) + "\n" for item in results),
        encoding="utf-8",
    )

    exit_code = main(
        [
            "--results-jsonl",
            str(results_path),
            "--output",
            str(output_path),
            "--commit",
            "abc123",
        ]
    )

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert exit_code == 1
    assert payload["eligible_for_95"] is False
    assert payload["gates"]["security_scan"]["status"] == "failed"


def test_security_scan_reports_location_without_secret_value() -> None:
    token = "sk-" + "proj-" + "A" * 32

    findings = find_secret_locations({"config.py": f'API_KEY = "{token}"', "example.py": "API_KEY = '<placeholder>'"})

    assert findings == ["config.py:1"]
    assert token not in " ".join(findings)


def test_security_scan_only_ignores_the_exact_synthetic_redaction_fixture() -> None:
    synthetic = "sk-" "proj-abcdefghijklmnopqrstuvwxyz123456"
    real_looking = "sk-" "proj-abcdefghijklmnopqrstuvwxyz123457"

    findings = find_secret_locations(
        {
            "tests/test_hermes_skill_distillation.py": f'secret = "{synthetic}"\nsecret = "{real_looking}"',
            "tests/test_hermes_skill_distillation.py:copy": f'secret = "{synthetic}"',
        }
    )

    assert findings == [
        "tests/test_hermes_skill_distillation.py:2",
        "tests/test_hermes_skill_distillation.py:copy:1",
    ]


def test_load_probe_exercises_gateway_with_unique_traces() -> None:
    report = run_load_test(requests=24, concurrency=6, max_p95_ms=1000)

    assert report["ok"] is True
    assert report["requests"] == 24
    assert report["failures"] == 0
    assert report["unique_trace_ids"] == 24


def test_backup_restore_check_preserves_canary_and_revision(tmp_path) -> None:
    report = run_backup_restore_check(tmp_path)

    assert report["ok"] is True
    assert report["source_revision"] == report["restored_revision"]
    assert report["canary_tg_user_id"] == 950000001


def test_release_shell_is_valid_and_declares_every_required_gate() -> None:
    script = Path("scripts/release_95_gate.sh")

    syntax = subprocess.run(["bash", "-n", str(script)], check=False)
    source = script.read_text(encoding="utf-8")

    assert syntax.returncode == 0
    assert all(f'run_gate "{name}"' in source for name in REQUIRED_GATES)
    assert "--require-live" in source
    assert "release_scorecard.py" in source
    assert source.index('run_gate "live_telegram_e2e"') < source.index('run_gate "provider_benchmark"')
