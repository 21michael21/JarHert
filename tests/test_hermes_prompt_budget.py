from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_soul_is_a_compact_behavior_layer_not_a_second_tool_manual() -> None:
    soul = (ROOT / "hermes" / "SOUL.md").read_text(encoding="utf-8")

    assert len(soul.encode("utf-8")) <= 6_500
    assert "до 320 символов" in soul
    assert "Не отправляй служебное «принял, обрабатываю»" in soul
    assert "одну точку подтверждения" in soul
    assert "Не говори, что" in soul
    assert "Не сохраняй пароли, токены" in soul
    assert "mcp_jarhert_native_action_plan_confirm_execute" in soul
