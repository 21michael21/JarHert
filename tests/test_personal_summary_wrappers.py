from __future__ import annotations

from pathlib import Path


def test_daily_and_weekly_wrappers_fix_the_summary_kind_without_shell_arguments() -> None:
    root = Path(__file__).resolve().parents[1]
    daily = (root / "hermes" / "scripts" / "dispatch_daily_brief.py").read_text(encoding="utf-8")
    weekly = (root / "hermes" / "scripts" / "dispatch_weekly_review.py").read_text(encoding="utf-8")

    assert '"--kind", "daily"' in daily
    assert '"--kind", "weekly"' in weekly
    assert "dispatch_personal_summary.py" in daily
    assert "dispatch_personal_summary.py" in weekly
