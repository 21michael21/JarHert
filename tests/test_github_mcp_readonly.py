from __future__ import annotations

from pathlib import Path

from hermes.native_tools.github_mcp import GITHUB_READ_ONLY_TOOLSETS, github_mcp_status


def test_github_mcp_is_disabled_until_an_owner_explicitly_enables_it(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("GITHUB_MCP_ENABLED", raising=False)
    monkeypatch.delenv("GITHUB_PERSONAL_ACCESS_TOKEN", raising=False)

    status = github_mcp_status(profile_home=tmp_path)

    assert status == {
        "state": "disabled",
        "enabled": False,
        "read_only": True,
        "toolsets": list(GITHUB_READ_ONLY_TOOLSETS),
    }


def test_github_mcp_requires_both_a_token_and_the_official_binary(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_MCP_ENABLED", "true")
    monkeypatch.delenv("GITHUB_PERSONAL_ACCESS_TOKEN", raising=False)

    assert github_mcp_status(profile_home=tmp_path)["state"] == "needs_token"

    monkeypatch.setenv("GITHUB_PERSONAL_ACCESS_TOKEN", "test-token")
    assert github_mcp_status(profile_home=tmp_path)["state"] == "missing_binary"

    binary = tmp_path / "bin" / "github-mcp-server"
    binary.parent.mkdir()
    binary.write_text("binary placeholder", encoding="utf-8")
    binary.chmod(0o700)

    status = github_mcp_status(profile_home=tmp_path)

    assert status["state"] == "ready"
    assert status["enabled"] is True
    assert status["read_only"] is True


def test_profile_declares_a_strict_read_only_github_mcp_and_nightly_consolidation_timer() -> None:
    root = Path(__file__).resolve().parents[1]
    config = (root / "hermes" / "config.yaml").read_text(encoding="utf-8")
    skill = (root / "hermes" / "skills" / "github-research" / "SKILL.md").read_text(encoding="utf-8")
    installer = (root / "deploy" / "vps" / "install_personal_summary_timers.sh").read_text(encoding="utf-8")
    timer = (root / "deploy" / "vps" / "systemd" / "hermes-memory-consolidation.timer").read_text(encoding="utf-8")
    gateway_check = (root / "deploy" / "vps" / "verify_single_telegram_gateway.sh").read_text(encoding="utf-8")
    installer_script = (root / "deploy" / "vps" / "install_github_mcp_readonly.sh").read_text(encoding="utf-8")
    dashboard = (root / "hermes" / "native_tools" / "dashboard_assets" / "dashboard.js").read_text(encoding="utf-8")

    assert "github_readonly:" in config
    assert "--read-only" in config
    assert "repos,issues,pull_requests,actions,users,code_security" in config
    assert "enabled: false" in config
    assert "не создавай" in skill.casefold()
    assert "не удаляй" in skill.casefold()
    assert "hermes-memory-consolidation.timer" in installer
    assert "OnCalendar=*-*-* 03:20:00" in timer
    assert "gateway_bot[.]telegram_app" in gateway_check
    assert "LEGACY_GATEWAY_UNIT" in gateway_check
    assert "single_gateway_ok=true" in gateway_check
    assert "github/github-mcp-server.git" in installer_script
    assert "go -C" in installer_script
    assert "GITHUB_MCP_ENABLED=true" in installer_script
    assert 'statusRow("GitHub"' in dashboard
    assert "githubMcpLabel" in dashboard
