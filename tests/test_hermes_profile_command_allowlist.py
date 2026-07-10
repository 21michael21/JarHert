from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_profile_only_allowlists_structured_native_cli() -> None:
    config = (ROOT / "hermes" / "config.yaml").read_text(encoding="utf-8")

    assert "approvals:\n  mode: manual\n  timeout: 60\n  cron_mode: deny" in config
    assert "command_allowlist:\n  - 'python \"$HERMES_HOME/native_tools/cli.py\" *'" in config
    assert config.count("command_allowlist:") == 1
