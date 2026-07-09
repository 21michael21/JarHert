from pathlib import Path

from scripts.eval_golden import evaluate_files


def test_golden_dialogs_pass() -> None:
    results = evaluate_files(sorted(Path("tests/golden_dialogs").glob("*.json")))

    passed = sum(1 for result in results if result.ok)
    pass_rate = passed / len(results)

    assert len(results) >= 100
    assert pass_rate >= 0.95, [result for result in results if not result.ok]
