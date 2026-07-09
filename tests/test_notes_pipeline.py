from __future__ import annotations

from assistant.hermes_client import FakeHermesClient
from assistant.limits import DailyLimitStore
from assistant.personal_knowledge import InMemoryPersonalKnowledgeStore
from assistant.pipeline import AssistantPipeline
from assistant.types import UserContext


def user(user_id: int = 1) -> UserContext:
    return UserContext(user_id=user_id, tg_user_id=1000 + user_id)


def make_pipeline() -> AssistantPipeline:
    return AssistantPipeline(
        FakeHermesClient(),
        DailyLimitStore(),
        plain_text_ai_enabled=True,
        knowledge=InMemoryPersonalKnowledgeStore(),
    )


def test_notes_command_create_list_search_edit_delete() -> None:
    pipeline = make_pipeline()

    created = pipeline.handle_text(user(), "/notes OAuth refresh | type=memory | project=JarHert | contact=Илья")
    listed = pipeline.handle_text(user(), "/notes")
    found = pipeline.handle_text(user(), "/notes search oauth")
    edited = pipeline.handle_text(user(), "/notes edit last OAuth refresh перед деплоем")
    deleted = pipeline.handle_text(user(), "/notes delete last")
    empty = pipeline.handle_text(user(), "/notes")

    assert "Сохранил заметку #1" in created.text
    assert "JarHert" in listed.text
    assert "Илья" in listed.text
    assert "OAuth refresh" in found.text
    assert "Обновил заметку #1" in edited.text
    assert "Удалил заметку #1" in deleted.text
    assert "Заметок пока нет" in empty.text


def test_natural_notes_phrases_use_personal_knowledge() -> None:
    pipeline = make_pipeline()

    saved = pipeline.handle_text(user(), "сохрани OAuth надо обновить перед деплоем")
    found = pipeline.handle_text(user(), "найди заметки про OAuth")
    edited = pipeline.handle_text(user(), "измени последнюю на OAuth обновить после теста")
    deleted = pipeline.handle_text(user(), "удали её")

    assert "Сохранил заметку #1" in saved.text
    assert "OAuth надо обновить" in found.text
    assert "Обновил заметку #1" in edited.text
    assert "Удалил заметку #1" in deleted.text


def test_notes_are_user_scoped() -> None:
    pipeline = make_pipeline()

    pipeline.handle_text(user(1), "сохрани личная OAuth заметка")
    own = pipeline.handle_text(user(1), "найди заметки про OAuth")
    other = pipeline.handle_text(user(2), "найди заметки про OAuth")

    assert "личная OAuth заметка" in own.text
    assert "личная OAuth заметка" not in other.text
    assert "Не нашёл заметки" in other.text
