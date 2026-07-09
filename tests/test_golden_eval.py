from pathlib import Path

from scripts.eval_golden import evaluate_files


def test_golden_dialogs_pass() -> None:
    results = evaluate_files([Path("tests/golden_dialogs/natural_ux.json")])

    assert len(results) >= 30
    assert all(result.ok for result in results), [result for result in results if not result.ok]
