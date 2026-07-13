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


def test_merge_adds_only_managed_native_send_env_without_touching_live_model(tmp_path: Path) -> None:
    source = tmp_path / "source.yaml"
    target = tmp_path / "target.yaml"
    source.write_text(
        "model:\n  provider: openai-api\nmcp_servers:\n  jarhert_native:\n    env:\n"
        "      HERMES_NATIVE_SEND_COMMAND: \"${HERMES_NATIVE_SEND_COMMAND}\"\n"
        "    tools:\n      include:\n        - integration_health\n",
        encoding="utf-8",
    )
    target.write_text(
        "model:\n  provider: openai-codex\n  default: gpt-5.4-mini\nmcp_servers:\n  jarhert_native:\n"
        "    env:\n      HERMES_HOME: \"${HERMES_HOME}\"\n    tools:\n      include:\n        - integration_health\n",
        encoding="utf-8",
    )

    assert merge_profile_config(source, target) == ["env:HERMES_NATIVE_SEND_COMMAND"]
    updated = target.read_text(encoding="utf-8")
    assert "provider: openai-codex" in updated
    assert "HERMES_NATIVE_SEND_COMMAND: \"${HERMES_NATIVE_SEND_COMMAND}\"" in updated


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
        "  provider: local\n  local:\n    model: small\n",
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
    assert "stt:\n  enabled: true\n  echo_transcripts: false\n  provider: local\n  local:\n    model: small\n" in updated


def test_merge_keeps_live_stt_when_it_already_exists(tmp_path: Path) -> None:
    source = tmp_path / "source.yaml"
    target = tmp_path / "target.yaml"
    source.write_text("stt:\n  enabled: true\n  provider: local\n", encoding="utf-8")
    target.write_text("stt:\n  enabled: false\n  provider: openai\n", encoding="utf-8")

    assert merge_profile_config(source, target) == []
    assert target.read_text(encoding="utf-8") == "stt:\n  enabled: false\n  provider: openai\n"


def test_merge_adds_telegram_final_answer_display_defaults_without_touching_model(tmp_path: Path) -> None:
    source = tmp_path / "source.yaml"
    target = tmp_path / "target.yaml"
    source.write_text(
        "model:\n  provider: openai-api\n"
        "display:\n"
        "  busy_input_mode: queue\n"
        "  busy_ack_enabled: false\n"
        "  platforms:\n"
        "    telegram:\n"
        "      tool_progress: off\n"
        "      interim_assistant_messages: false\n"
        "      long_running_notifications: false\n"
        "      cleanup_progress: true\n",
        encoding="utf-8",
    )
    target.write_text("model:\n  provider: openai-codex\n  default: gpt-5.4-mini\n", encoding="utf-8")

    assert merge_profile_config(source, target) == ["display"]
    updated = target.read_text(encoding="utf-8")
    assert "provider: openai-codex" in updated
    assert "default: gpt-5.4-mini" in updated
    assert "busy_input_mode: queue" in updated
    assert "interim_assistant_messages: false" in updated


def test_merge_keeps_existing_live_display_choices(tmp_path: Path) -> None:
    source = tmp_path / "source.yaml"
    target = tmp_path / "target.yaml"
    source.write_text("display:\n  busy_ack_enabled: false\n", encoding="utf-8")
    target.write_text("display:\n  busy_ack_enabled: true\n", encoding="utf-8")

    assert merge_profile_config(source, target) == []
    assert target.read_text(encoding="utf-8") == "display:\n  busy_ack_enabled: true\n"


def test_merge_adds_disabled_read_only_github_mcp_without_touching_live_model(tmp_path: Path) -> None:
    source = tmp_path / "source.yaml"
    target = tmp_path / "target.yaml"
    source.write_text(
        "model:\n  provider: openai-api\nmcp_servers:\n"
        "  github_readonly:\n"
        "    command: github-mcp-server\n"
        "    args:\n      - stdio\n      - --read-only\n"
        "    enabled: false\n",
        encoding="utf-8",
    )
    target.write_text("model:\n  provider: openai-codex\n  default: gpt-5.4-mini\n", encoding="utf-8")

    assert merge_profile_config(source, target) == ["mcp:github_readonly"]
    updated = target.read_text(encoding="utf-8")
    assert "provider: openai-codex" in updated
    assert "github_readonly:" in updated
    assert "--read-only" in updated
    assert "enabled: false" in updated
