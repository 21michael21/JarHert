from __future__ import annotations

from pathlib import Path
import subprocess
import sys


def test_daily_and_weekly_wrappers_fix_the_summary_kind_without_shell_arguments() -> None:
    root = Path(__file__).resolve().parents[1]
    daily = (root / "hermes" / "scripts" / "dispatch_daily_brief.py").read_text(encoding="utf-8")
    weekly = (root / "hermes" / "scripts" / "dispatch_weekly_review.py").read_text(encoding="utf-8")

    assert '"--kind", "daily"' in daily
    assert '"--kind", "weekly"' in weekly
    assert "dispatch_personal_summary.py" in daily
    assert "dispatch_personal_summary.py" in weekly


def test_daily_read_reminder_is_a_deterministic_script_only_message() -> None:
    root = Path(__file__).resolve().parents[1]
    script = root / "hermes" / "scripts" / "daily_read_reminder.py"

    result = subprocess.run([sys.executable, str(script)], check=True, capture_output=True, text=True)

    assert result.stdout == "пора читать гнида\n"
