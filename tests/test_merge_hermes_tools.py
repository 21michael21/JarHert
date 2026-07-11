from __future__ import annotations

from pathlib import Path

from deploy.vps.merge_hermes_tools import merge_profile_config


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

    assert merge_profile_config(source, target) == ["tool:system_status"]
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

    assert merge_profile_config(source, target) == []


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

    assert merge_profile_config(source, target) == ["tool:system_status"]
    updated = target.read_text(encoding="utf-8")
    assert "      - integration_health" in updated
    assert "      - system_status" in updated
    assert "        - system_status" not in updated


def test_merge_adds_managed_local_stt_without_overwriting_live_model(tmp_path: Path) -> None:
    source = tmp_path / "source.yaml"
    target = tmp_path / "target.yaml"
    source.write_text(
        "model:\n  provider: openai-api\nstt:\n  enabled: true\n  echo_transcripts: false\n"
        "  provider: local\n  local:\n    model: base\n",
        encoding="utf-8",
    )
    target.write_text(
        "model:\n  provider: openai-codex\n  default: gpt-5.4-mini\nmcp_servers:\n"
        "  jarhert_native:\n    tools:\n      include:\n        - integration_health\n",
        encoding="utf-8",
    )

    assert merge_profile_config(source, target) == ["stt"]
    updated = target.read_text(encoding="utf-8")
    assert "provider: openai-codex" in updated
    assert "default: gpt-5.4-mini" in updated
    assert "stt:\n  enabled: true\n  echo_transcripts: false\n  provider: local\n  local:\n    model: base\n" in updated


def test_merge_keeps_live_stt_when_it_already_exists(tmp_path: Path) -> None:
    source = tmp_path / "source.yaml"
    target = tmp_path / "target.yaml"
    source.write_text("stt:\n  enabled: true\n  provider: local\n", encoding="utf-8")
    target.write_text("stt:\n  enabled: false\n  provider: openai\n", encoding="utf-8")

    assert merge_profile_config(source, target) == []
    assert target.read_text(encoding="utf-8") == "stt:\n  enabled: false\n  provider: openai\n"
