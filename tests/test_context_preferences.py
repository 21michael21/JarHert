from __future__ import annotations

from assistant.action_queue import ActionStatus, InMemoryActionQueueStore
from assistant.context_store import InMemoryConversationStore
from assistant.hermes_client import FakeHermesClient
from assistant.ideas import InMemoryIdeaStore
from assistant.limits import DailyLimitStore
from assistant.pipeline import AssistantPipeline
from assistant.preferences import InMemoryPreferenceStore
from assistant.types import UserContext
from backend.db import init_db, make_session_factory
from backend.stores import SqlConversationStore, SqlUserPreferenceStore, UserStore
from reminders.store import InMemoryReminderStore


class FakeTaskCenter:
    def __init__(self) -> None:
        self.calls = []

    def create_task(self, text):
        self.calls.append(("task", text))
        return "Created Trello card"

    def create_task_with_calendar(self, **kwargs):
        self.calls.append(("task_with_calendar", kwargs))
        return "Created task with calendar"

    def create_calendar_event(self, text):
        self.calls.append(("calendar", text))
        return "Created calendar event"

    def list_tasks(self, text):
        self.calls.append(("tasks", text))
        return "Task list"


def session_factory(tmp_path):
    factory = make_session_factory(f"sqlite:///{tmp_path / 'test.sqlite3'}")
    init_db(factory)
    return factory


def user(user_id: int = 1) -> UserContext:
    return UserContext(user_id=user_id, tg_user_id=10_000 + user_id)


def execute_confirmed_actions(pipeline: AssistantPipeline, queue: InMemoryActionQueueStore) -> list[str]:
    results: list[str] = []
    for action in queue.list_for_user(user().user_id, limit=20):
        if action.status == ActionStatus.NEEDS_CONFIRMATION:
            assert queue.confirm_for_user(user().user_id, action.id) is not None
    while True:
        action = queue.claim_next()
        if action is None:
            return results
        results.append(pipeline.execute_queued_action(user(), action))
        queue.mark_succeeded(action.id)


def test_sql_conversation_turns_persist_and_are_user_scoped(tmp_path) -> None:
    factory = session_factory(tmp_path)
    users = UserStore(factory)
    user_one = users.get_or_create(9201)
    user_two = users.get_or_create(9202)
    store_one = SqlConversationStore(factory)
    store_two = SqlConversationStore(factory)

    store_one.add(
        user_id=user_one.id,
        user_text="Утренний дайджест полезен",
        assistant_text="Принял.",
        extracted_actions=[{"type": "idea.save", "payload": {"text": "Утренний дайджест полезен"}}],
    )
    store_one.add(user_id=user_two.id, user_text="чужой текст", assistant_text="ok", extracted_actions=[])

    turns = store_two.list_recent(user_one.id)

    assert len(turns) == 1
    assert turns[0].user_text == "Утренний дайджест полезен"
    assert turns[0].extracted_actions[0]["type"] == "idea.save"
    assert store_two.latest_user_text(user_two.id) == "чужой текст"


def test_pipeline_uses_previous_text_for_context_idea() -> None:
    context = InMemoryConversationStore()
    ideas = InMemoryIdeaStore()
    pipeline = AssistantPipeline(
        FakeHermesClient(),
        DailyLimitStore(),
        plain_text_ai_enabled=True,
        ideas=ideas,
        conversation_turns=context,
    )

    pipeline.handle_text(user(), "Утренний дайджест полезен для фокуса")
    reply = pipeline.handle_text(user(), "запиши это как идею")

    assert "Сделал" in reply.text
    assert ideas.list_for_user(1)[0].text == "Утренний дайджест полезен для фокуса"
    assert context.list_recent(1)[0].extracted_actions[0]["type"] == "idea.save"


def test_ambiguous_pronoun_move_asks_for_object() -> None:
    pipeline = AssistantPipeline(
        FakeHermesClient(),
        DailyLimitStore(),
        plain_text_ai_enabled=True,
        conversation_turns=InMemoryConversationStore(),
    )

    reply = pipeline.handle_text(user(), "перенеси её на завтра")

    assert reply.blocked_reason == "natural_action_needs_clarification"
    assert "Уточни" in reply.text


def test_sql_user_preferences_persist(tmp_path) -> None:
    factory = session_factory(tmp_path)
    user_record = UserStore(factory).get_or_create(9301)
    store_one = SqlUserPreferenceStore(factory)
    store_two = SqlUserPreferenceStore(factory)

    updated = store_one.update(
        user_record.id,
        default_trello_list="Today",
        evening_time="20:15",
        preferred_response_style="short",
    )

    loaded = store_two.get(user_record.id)
    assert updated.default_trello_list == "Today"
    assert loaded.default_trello_list == "Today"
    assert loaded.evening_time == "20:15"
    assert loaded.preferred_response_style == "short"


def test_expressive_style_preference_can_be_enabled_and_disabled() -> None:
    store = InMemoryPreferenceStore()
    pipeline = AssistantPipeline(
        FakeHermesClient(),
        DailyLimitStore(),
        preferences=store,
    )

    expressive = pipeline.handle_text(user(), "пиши живее можно с матом")
    concise = pipeline.handle_text(user(), "без мата отвечай нормально")

    assert "живее" in expressive.text
    assert "без мата" in concise.text
    assert store.get(1).preferred_response_style == "concise"


def test_preference_updates_default_task_list_and_task_uses_it() -> None:
    task_center = FakeTaskCenter()
    pipeline = AssistantPipeline(
        FakeHermesClient(),
        DailyLimitStore(),
        preferences=InMemoryPreferenceStore(),
        task_center=task_center,
    )

    first = pipeline.handle_text(user(), "по умолчанию задачи в Today")
    second = pipeline.handle_text(user(), "создай задачу проверить сервер")

    assert "Сохранил настройку" in first.text
    assert "Создал задачу" in second.text
    assert task_center.calls == [("task", "проверить сервер | list=Today")]


def test_evening_preference_changes_natural_calendar_time() -> None:
    queue = InMemoryActionQueueStore()
    task_center = FakeTaskCenter()
    pipeline = AssistantPipeline(
        FakeHermesClient(),
        DailyLimitStore(),
        plain_text_ai_enabled=True,
        preferences=InMemoryPreferenceStore(),
        task_center=task_center,
        action_queue=queue,
    )

    pipeline.handle_text(user(), "вечером это 20:15")
    reply = pipeline.handle_text(user(), "завтра вечером созвон с Ильей")
    results = execute_confirmed_actions(pipeline, queue)

    assert "Нужно одно подтверждение" in reply.text
    assert len(results) == 1
    assert task_center.calls == [
        ("calendar", "созвон с Ильей | start=tomorrow 20:15 | end=tomorrow 20:45")
    ]


def test_default_reminder_time_preference_is_used() -> None:
    reminders = InMemoryReminderStore()
    pipeline = AssistantPipeline(
        FakeHermesClient(),
        DailyLimitStore(),
        reminders=reminders,
        preferences=InMemoryPreferenceStore(),
    )

    pipeline.handle_text(user(), "напоминания по умолчанию в 11:30")
    reply = pipeline.handle_text(user(), "/remind до завтра проверить OAuth")

    assert "Поставил напоминание" in reply.text
    assert reminders.list_pending_for_user(1)[0].remind_at.hour == 11
    assert reminders.list_pending_for_user(1)[0].remind_at.minute == 30
