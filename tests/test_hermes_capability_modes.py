from __future__ import annotations

from pathlib import Path

from hermes.native_tools.capabilities import CapabilityPolicyStore
from hermes.native_tools.mcp_api import NativeToolsAPI


ROOT = Path(__file__).resolve().parents[1]


def test_fast_mode_uses_low_reasoning_and_blocks_code_workspace(tmp_path: Path) -> None:
    policy = CapabilityPolicyStore(tmp_path / "personal-os.sqlite3")

    mode = policy.get_mode()

    assert mode.name == "fast"
    assert mode.reasoning_effort == "low"
    assert mode.timeout_seconds == 45
    assert policy.decide("task.list").decision == "auto"
    assert policy.decide("task.create").decision == "confirm"
    assert policy.decide("task.delete").decision == "preview"
    assert policy.decide("research.run").decision == "preview"
    assert policy.decide("sandbox.run").decision == "deny"


def test_think_and_code_modes_change_capabilities_and_timeout(tmp_path: Path) -> None:
    policy = CapabilityPolicyStore(tmp_path / "personal-os.sqlite3")

    think = policy.set_mode("think")
    think_research = policy.decide("research.run")
    code = policy.set_mode("code")
    code_sandbox = policy.decide("sandbox.run")

    assert (think.reasoning_effort, think.timeout_seconds) == ("high", 180)
    assert think_research.decision == "preview"
    assert (code.reasoning_effort, code.timeout_seconds) == ("high", 900)
    assert code_sandbox.decision == "preview"
    assert CapabilityPolicyStore(policy.database_path).get_mode().name == "code"


def test_unknown_capability_is_denied(tmp_path: Path) -> None:
    policy = CapabilityPolicyStore(tmp_path / "personal-os.sqlite3")

    decision = policy.decide("root.shell")

    assert decision.decision == "deny"
    assert decision.reason == "capability_not_allowlisted"


def test_native_api_exposes_mode_without_exposing_policy_mutation(tmp_path: Path) -> None:
    api = NativeToolsAPI(database_path=tmp_path / "personal-os.sqlite3")

    assert api.work_mode_get()["name"] == "fast"
    assert api.work_mode_set(mode="think")["name"] == "think"
    assert api.capability_decision(capability="sandbox.run")["decision"] == "deny"


def test_profile_defaults_to_fast_reasoning_and_exposes_mode_tools() -> None:
    config = (ROOT / "hermes" / "config.yaml").read_text(encoding="utf-8")
    soul = (ROOT / "hermes" / "SOUL.md").read_text(encoding="utf-8")

    assert "agent:\n  reasoning_effort: low" in config
    assert "- work_mode_get" in config
    assert "- work_mode_set" in config
    assert "mcp_jarhert_native_work_mode_set" in soul
