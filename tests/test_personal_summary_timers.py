from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_personal_summary_timers_use_profile_env_and_deterministic_scripts() -> None:
    daily_service = (ROOT / "deploy" / "vps" / "systemd" / "hermes-daily-brief.service").read_text(encoding="utf-8")
    daily_timer = (ROOT / "deploy" / "vps" / "systemd" / "hermes-daily-brief.timer").read_text(encoding="utf-8")
    weekly_service = (ROOT / "deploy" / "vps" / "systemd" / "hermes-weekly-review.service").read_text(encoding="utf-8")
    weekly_timer = (ROOT / "deploy" / "vps" / "systemd" / "hermes-weekly-review.timer").read_text(encoding="utf-8")

    for service in (daily_service, weekly_service):
        assert "EnvironmentFile=%h/.hermes/profiles/jarhert/.env" in service
        assert "dispatch_personal_summary.py" in service
        assert "--no-agent" not in service
    assert "--kind daily" in daily_service
    assert "OnCalendar=*-*-* 09:00:00" in daily_timer
    assert "--kind weekly" in weekly_service
    assert "OnCalendar=Sun *-*-* 18:00:00" in weekly_timer
    assert "Persistent=true" in daily_timer
    assert "Persistent=true" in weekly_timer
