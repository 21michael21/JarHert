from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_telegram_approval_patch_localizes_and_hides_operator_name(tmp_path: Path) -> None:
    target = tmp_path / "adapter.py"
    target.write_text(
        '''label_map = {
    "once": "✅ Approved once",
    "session": "✅ Approved for session",
    "always": "✅ Approved permanently",
    "deny": "❌ Denied",
}
await query.edit_message_text(text=self.format_message(f"{label} by {user_display}"),)
label_map = {
    "once": "✅ Approved once",
    "always": "🔒 Always approve",
    "cancel": "❌ Cancelled",
}
await query.edit_message_text(text=self.format_message(f"{label} by {user_display}"),)
''',
        encoding="utf-8",
    )

    subprocess.run(
        [sys.executable, str(ROOT / "deploy/vps/patch_hermes_telegram_approval.py"), str(target)],
        check=True,
        capture_output=True,
        text=True,
    )

    updated = target.read_text(encoding="utf-8")
    assert '"once": "✅ Подтверждено"' in updated
    assert "Approved once" not in updated
    assert 'f"{label} by {user_display}"' not in updated
