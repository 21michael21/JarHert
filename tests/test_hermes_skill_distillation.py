from __future__ import annotations

import pytest

from hermes.native_tools.mcp_api import NativeToolsAPI
from hermes.native_tools.skill_distillation import SkillDistiller


STEPS = [
    {"tool": "personal_operating_center", "summary": "Собрать календарь и задачи"},
    {"tool": "personal_memory", "summary": "Добавить обещания и блокеры"},
    {"tool": "telegram_delivery", "summary": "Отправить короткий итог"},
]


def make_distiller(tmp_path) -> SkillDistiller:
    return SkillDistiller(tmp_path / "personal-os.sqlite3")


def observe(distiller: SkillDistiller, number: int, **overrides):
    values = {
        "workflow_key": "morning-plan",
        "title": "Утренний план",
        "steps": STEPS,
        "idempotency_key": f"telegram-update-{number}",
        "success": True,
        "confirmed": True,
    }
    values.update(overrides)
    return distiller.observe(**values)


def test_three_unique_confirmed_successes_create_reviewable_skill(tmp_path) -> None:
    distiller = make_distiller(tmp_path)

    first = observe(distiller, 1)
    second = observe(distiller, 2)
    third = observe(distiller, 3)

    assert first.confirmation_count == 1
    assert first.status == "observing"
    assert second.confirmation_count == 2
    assert third.confirmation_count == 3
    assert third.status == "ready_for_review"
    assert third.skill_name == "learned-morning-plan"
    assert "# Утренний план" in third.skill_markdown
    assert "personal_operating_center" in third.skill_markdown


def test_replayed_confirmation_does_not_increment_count(tmp_path) -> None:
    distiller = make_distiller(tmp_path)

    first = observe(distiller, 1)
    replay = observe(distiller, 1)

    assert first.confirmation_count == replay.confirmation_count == 1
    assert len(distiller.list_candidates()) == 1


def test_failed_or_unconfirmed_result_does_not_count(tmp_path) -> None:
    distiller = make_distiller(tmp_path)

    failed = observe(distiller, 1, success=False)
    unconfirmed = observe(distiller, 2, confirmed=False)

    assert failed.confirmation_count == 0
    assert unconfirmed.confirmation_count == 0
    assert unconfirmed.status == "observing"


def test_unknown_tool_is_rejected(tmp_path) -> None:
    distiller = make_distiller(tmp_path)

    with pytest.raises(ValueError, match="allowlist"):
        observe(
            distiller,
            1,
            steps=[
                {"tool": "shell", "summary": "rm -rf /"},
                {"tool": "tests", "summary": "Скрыть последствия"},
            ],
        )


def test_sensitive_values_are_redacted_from_candidate(tmp_path) -> None:
    distiller = make_distiller(tmp_path)
    secret = "sk-proj-abcdefghijklmnopqrstuvwxyz123456"
    steps = [
        {
            "tool": "files",
            "summary": f"Открыть /Users/alice/private/.env и взять {secret}",
        },
        {"tool": "tests", "summary": "Проверить результат"},
    ]

    candidate = observe(distiller, 1, steps=steps)

    assert secret not in candidate.skill_markdown
    assert "/Users/alice" not in candidate.skill_markdown
    assert "[REDACTED]" in candidate.skill_markdown


def test_same_key_cannot_merge_a_different_procedure(tmp_path) -> None:
    distiller = make_distiller(tmp_path)
    observe(distiller, 1)

    with pytest.raises(ValueError, match="другая процедура"):
        observe(
            distiller,
            2,
            steps=[
                {"tool": "calendar", "summary": "Создать встречу"},
                {"tool": "telegram_delivery", "summary": "Сообщить об итоге"},
            ],
        )


def test_native_api_returns_reviewable_skill_after_three_feedback_events(tmp_path) -> None:
    api = NativeToolsAPI(database_path=tmp_path / "personal-os.sqlite3")

    results = [
        api.skill_feedback(
            workflow_key="morning-plan",
            title="Утренний план",
            steps=STEPS,
            idempotency_key=f"telegram-feedback-{index}",
            useful=True,
        )
        for index in range(3)
    ]

    assert results[0]["status"] == "observing"
    assert results[2]["status"] == "ready_for_review"
    assert results[2]["confirmation_count"] == 3
    assert "# Утренний план" in results[2]["skill_markdown"]
    assert api.skill_candidates(ready_only=True)["items"] == [results[2]]
