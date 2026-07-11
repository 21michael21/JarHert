from __future__ import annotations

from pathlib import Path

from deploy.vps.merge_hermes_tools import merge_native_tool_allowlist


def test_merge_adds_only_missing_native_tools_and_keeps_live_model(tmp_path: Path) -> None:
    source = tmp_path / "source.yaml"
    target = tmp_path / "target.yaml"
    source.write_text(
        "model:\n  provider: openai-api\n  default: gpt-5-nano\nmcp_servers:\n  jarhert_native:\n    tools:\n      include:\n        - integration_health\n        - system_status\n",
        encoding="utf-8",
    )
    target.write_text(
        "model:\n  provider: openai-codex\n  default: gpt-5.4-mini\nmcp_servers:\n  jarhert_native:\n    tools:\n      include:\n        - integration_health\n",
        encoding="utf-8",
    )

    assert merge_native_tool_allowlist(source, target) == ["system_status"]
    updated = target.read_text(encoding="utf-8")
    assert "provider: openai-codex" in updated
    assert "default: gpt-5.4-mini" in updated
    assert updated.count("- system_status") == 1


def test_merge_is_idempotent(tmp_path: Path) -> None:
    source = tmp_path / "source.yaml"
    target = tmp_path / "target.yaml"
    content = "mcp_servers:\n  jarhert_native:\n    tools:\n      include:\n        - integration_health\n"
    source.write_text(content, encoding="utf-8")
    target.write_text(content, encoding="utf-8")

    assert merge_native_tool_allowlist(source, target) == []


def test_merge_preserves_legacy_yaml_list_indentation(tmp_path: Path) -> None:
    source = tmp_path / "source.yaml"
    target = tmp_path / "target.yaml"
    source.write_text(
        "mcp_servers:\n  jarhert_native:\n    tools:\n      include:\n        - integration_health\n        - system_status\n      resources: false\n",
        encoding="utf-8",
    )
    target.write_text(
        "model:\n  provider: openai-codex\nmcp_servers:\n  jarhert_native:\n    tools:\n      include:\n      - integration_health\n      resources: false\n",
        encoding="utf-8",
    )

    assert merge_native_tool_allowlist(source, target) == ["system_status"]
    updated = target.read_text(encoding="utf-8")
    assert "      - integration_health" in updated
    assert "      - system_status" in updated
    assert "        - system_status" not in updated
