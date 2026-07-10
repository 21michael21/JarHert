from assistant.action_queue import ActionStatus, InMemoryActionQueueStore
from assistant.hermes_client import FakeHermesClient
from assistant.ideas import InMemoryIdeaStore
from assistant.limits import DailyLimitStore
from assistant.memory import InMemoryMemoryStore
from assistant.pipeline import AssistantPipeline
from assistant.types import HermesResponse, UserContext
from reminders.store import InMemoryReminderStore


class FakeDocsSync:
    def __init__(self) -> None:
        self.items = []

    def append(self, *, kind, user_id, text, created_at=None, record_id=None) -> bool:
        self.items.append((kind, user_id, text))
        return True


class FakeTaskCenter:
    def __init__(self) -> None:
        self.calls = []

    def create_task(self, text):
        self.calls.append(("task", text))
        return "Created Trello card"

    def list_tasks(self, text):
        self.calls.append(("tasks", text))
        return "Task list"

    def create_calendar_event(self, text):
        self.calls.append(("calendar", text))
        return "Created calendar event"

    def create_task_with_calendar(self, **kwargs):
        self.calls.append(("task_with_calendar", kwargs))
        return "Created Trello card and calendar event"


def make_pipeline() -> AssistantPipeline:
    return AssistantPipeline(
        FakeHermesClient(),
        DailyLimitStore(),
        memories=InMemoryMemoryStore(),
        ideas=InMemoryIdeaStore(),
        reminders=InMemoryReminderStore(),
    )


def user(user_id: int = 1) -> UserContext:
    return UserContext(user_id=user_id, tg_user_id=1000 + user_id)


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


def test_remember_and_list_memories_are_user_scoped() -> None:
    pipeline = make_pipeline()
    assert "Сохранил" in pipeline.handle_text(user(1), "/remember купить молоко").text
    assert "Сохранил" in pipeline.handle_text(user(2), "/remember чужая заметка").text

    own = pipeline.handle_text(user(1), "/memories").text
    other = pipeline.handle_text(user(2), "/memories").text

    assert "купить молоко" in own
    assert "чужая заметка" not in own
    assert "чужая заметка" in other


def test_idea_and_list_ideas_are_user_scoped() -> None:
    pipeline = make_pipeline()
    assert "Сохранил идею" in pipeline.handle_text(user(1), "/idea голосовой inbox").text
    assert "Сохранил идею" in pipeline.handle_text(user(2), "идея чужая идея").text

    own = pipeline.handle_text(user(1), "/ideas").text
    other = pipeline.handle_text(user(2), "/ideas").text

    assert "голосовой inbox" in own
    assert "чужая идея" not in own
    assert "чужая идея" in other


def test_idea_syncs_to_docs_when_configured() -> None:
    docs = FakeDocsSync()
    pipeline = AssistantPipeline(
        FakeHermesClient(),
        DailyLimitStore(),
        ideas=InMemoryIdeaStore(),
        docs_sync=docs,
    )

    reply = pipeline.handle_text(user(1), "/idea проверить append в Google Docs")

    assert "Google Docs" in reply.text
    assert docs.items == [("idea", 1, "проверить append в Google Docs")]


def test_reminder_syncs_to_docs_when_configured() -> None:
    docs = FakeDocsSync()
    pipeline = AssistantPipeline(
        FakeHermesClient(),
        DailyLimitStore(),
        reminders=InMemoryReminderStore(),
        docs_sync=docs,
    )

    reply = pipeline.handle_text(user(1), "/remind 2026-07-09 09:30 проверить docs")

    assert "Google Docs" in reply.text
    assert docs.items[0][0] == "reminder"
    assert docs.items[0][1] == 1
    assert "проверить docs" in docs.items[0][2]


def test_remind_and_list_reminders() -> None:
    pipeline = make_pipeline()
    created = pipeline.handle_text(user(), "/remind 2026-07-09 09:30 проверить деплой")
    listed = pipeline.handle_text(user(), "/reminders")

    assert "Поставил напоминание" in created.text
    assert "проверить деплой" in listed.text


def test_natural_reminder_question_lists_existing_reminders() -> None:
    pipeline = make_pipeline()
    pipeline.handle_text(user(), "/remind 2026-07-09 09:30 заниматься ML")

    listed = pipeline.handle_text(user(), "Напоминалка стоит?")

    assert listed.intent.name == "REMINDERS"
    assert "заниматься ML" in listed.text


def test_live_style_reminder_phrase_creates_reminder_without_ai() -> None:
    reminders = InMemoryReminderStore()
    hermes = FakeHermesClient()
    pipeline = AssistantPipeline(
        hermes,
        DailyLimitStore(),
        plain_text_ai_enabled=True,
        reminders=reminders,
    )

    reply = pipeline.handle_text(
        user(),
        "Бро просто напоминалку чтобы я завтра в часов 12 дня напоминалка пришла что пора заниматься ml",
    )

    assert "Поставил напоминание" in reply.text
    assert "1. 1." not in reply.text
    assert reminders.list_pending_for_user(1)[0].text == "пора заниматься ml"
    assert reminders.list_pending_for_user(1)[0].remind_at.hour == 12
    assert reminders.list_pending_for_user(1)[0].remind_at.minute == 0
    assert hermes.requests == []


def test_chat_followup_uses_previous_reminder_request() -> None:
    reminders = InMemoryReminderStore()
    pipeline = AssistantPipeline(
        FakeHermesClient(),
        DailyLimitStore(),
        plain_text_ai_enabled=True,
        reminders=reminders,
    )

    pipeline.handle_text(
        user(),
        "Бро просто напоминалку чтобы я завтра в часов 12 дня напоминалка пришла что пора заниматься ml",
    )
    followup = pipeline.handle_text(user(), "Можно в чатик прислать уведомление")

    assert followup.intent.name == "REMINDERS"
    assert "пора заниматься ml" in followup.text
    assert len(reminders.list_pending_for_user(1)) == 1


def test_bad_reminder_time_is_clear_error() -> None:
    pipeline = make_pipeline()
    reply = pipeline.handle_text(user(), "/remind когда-нибудь проверить")
    assert reply.blocked_reason == "reminder_parse_failed"
    assert "Не понял время" in reply.text


def test_cancel_reminder() -> None:
    pipeline = make_pipeline()
    created = pipeline.handle_text(user(), "/remind 2026-07-09 09:30 проверить деплой")
    assert "Поставил" in created.text

    cancelled = pipeline.handle_text(user(), "/cancel_reminder 1")
    listed = pipeline.handle_text(user(), "/reminders")

    assert "Отменил" in cancelled.text
    assert "Активных напоминаний нет" in listed.text


def test_cancel_reminder_requires_numeric_id() -> None:
    pipeline = make_pipeline()
    reply = pipeline.handle_text(user(), "/cancel_reminder abc")
    assert reply.blocked_reason == "cancel_reminder_bad_id"


def test_task_command_uses_task_center() -> None:
    task_center = FakeTaskCenter()
    pipeline = AssistantPipeline(FakeHermesClient(), DailyLimitStore(), task_center=task_center)

    reply = pipeline.handle_text(user(), "/task проверить Trello | list=Today")

    assert "Создал задачу" in reply.text
    assert task_center.calls == [("task", "проверить Trello | list=Today")]


def test_calendar_command_uses_task_center() -> None:
    task_center = FakeTaskCenter()
    pipeline = AssistantPipeline(FakeHermesClient(), DailyLimitStore(), task_center=task_center)

    reply = pipeline.handle_text(user(), "/calendar созвон | start=2026-07-10 10:00 | end=2026-07-10 10:30")

    assert "Создал событие" in reply.text
    assert task_center.calls == [("calendar", "созвон | start=2026-07-10 10:00 | end=2026-07-10 10:30")]


def test_plain_task_batch_uses_task_center() -> None:
    queue = InMemoryActionQueueStore()
    task_center = FakeTaskCenter()
    pipeline = AssistantPipeline(
        FakeHermesClient(),
        DailyLimitStore(),
        plain_text_ai_enabled=True,
        task_center=task_center,
        action_queue=queue,
    )

    reply = pipeline.handle_text(
        user(),
        "завтра задача 1 проверить сервер в 10:00, задача 2 созвон в 12:00",
    )
    results = execute_confirmed_actions(pipeline, queue)
    queued = sorted(queue.list_for_user(user().user_id, limit=20), key=lambda action: action.id)

    assert "Нужно одно подтверждение" in reply.text
    assert reply.buttons[0][0].callback_data == "ai:confirm_job:1"
    assert len(results) == 2
    assert queued[1].depends_on_action_id == queued[0].id
    assert [call[0] for call in task_center.calls] == ["task_with_calendar", "task_with_calendar"]
    assert task_center.calls[0][1]["title"] == "проверить сервер"
    assert task_center.calls[0][1]["start"] == "tomorrow 10:00"
    assert task_center.calls[1][1]["title"] == "созвон"


def test_agent_do_creates_queued_job() -> None:
    pipeline = make_pipeline()

    reply = pipeline.handle_text(user(), "/do проверь Trello, поставь в календарь и покажи итог")
    listed = pipeline.handle_text(user(), "/jobs")
    details = pipeline.handle_text(user(), "/job 1")

    assert "Поставил в очередь job #1" in reply.text
    assert "Статус: queued" in reply.text
    assert "Trello" in reply.text
    assert "Очередь агента" in listed.text
    assert "Job #1" in details.text
    assert "календар" in details.text


def test_agent_jobs_are_user_scoped() -> None:
    pipeline = make_pipeline()

    assert "job #1" in pipeline.handle_text(user(1), "/do задача первого").text
    assert "Очередь агента пустая" in pipeline.handle_text(user(2), "/jobs").text
    assert pipeline.handle_text(user(2), "/job 1").blocked_reason == "agent_job_not_found"


def test_agent_job_requires_numeric_id() -> None:
    pipeline = make_pipeline()

    reply = pipeline.handle_text(user(), "/job abc")

    assert reply.blocked_reason == "agent_job_bad_id"


def test_plain_text_creates_task_and_calendar_without_tags() -> None:
    queue = InMemoryActionQueueStore()
    task_center = FakeTaskCenter()
    pipeline = AssistantPipeline(
        FakeHermesClient(),
        DailyLimitStore(),
        plain_text_ai_enabled=True,
        task_center=task_center,
        action_queue=queue,
    )

    reply = pipeline.handle_text(user(), "завтра в 10 проверь сервер и завтра в 12 созвон с Ильей")
    results = execute_confirmed_actions(pipeline, queue)

    assert "Нужно одно подтверждение" in reply.text
    assert len(results) == 2
    assert [call[0] for call in task_center.calls] == ["task_with_calendar", "calendar"]
    assert task_center.calls[0][1]["title"] == "проверь сервер"
    assert task_center.calls[0][1]["start"] == "tomorrow 10:00"
    assert task_center.calls[1][1] == "созвон с Ильей | start=tomorrow 12:00 | end=tomorrow 12:30"


def test_plain_text_mixed_idea_and_reminder_without_tags() -> None:
    docs = FakeDocsSync()
    pipeline = AssistantPipeline(
        FakeHermesClient(),
        DailyLimitStore(),
        plain_text_ai_enabled=True,
        ideas=InMemoryIdeaStore(),
        reminders=InMemoryReminderStore(),
        docs_sync=docs,
    )

    reply = pipeline.handle_text(user(), "запиши идею про Hub ML и напомни через 1 час обсудить")

    assert "Сделал" in reply.text
    assert "Сохранил идею" in reply.text
    assert "Поставил напоминание" in reply.text
    assert docs.items[0] == ("idea", 1, "про Hub ML")
    assert docs.items[1][0] == "reminder"


def test_plain_question_still_goes_to_ai() -> None:
    hermes = FakeHermesClient()
    pipeline = AssistantPipeline(
        hermes,
        DailyLimitStore(),
        plain_text_ai_enabled=True,
    )

    reply = pipeline.handle_text(user(), "что такое Hermes Agent простыми словами?")

    assert reply.provider == "fake"
    assert len(hermes.requests) == 1


def test_plain_text_llm_extractor_creates_action_when_deterministic_router_misses() -> None:
    queue = InMemoryActionQueueStore()
    task_center = FakeTaskCenter()
    hermes = FakeHermesClient(
        [
            HermesResponse(
                text='{"actions":[{"type":"task.create","payload":{"title":"ревью Hub ML","start":"tomorrow 09:00","end":"tomorrow 09:30"},"confidence":0.92}]}'
            )
        ]
    )
    pipeline = AssistantPipeline(
        hermes,
        DailyLimitStore(),
        plain_text_ai_enabled=True,
        task_center=task_center,
        action_queue=queue,
    )

    reply = pipeline.handle_text(user(), "организуй ревью Hub ML завтра утром")
    results = execute_confirmed_actions(pipeline, queue)

    assert "Нужно одно подтверждение" in reply.text
    assert len(results) == 1
    assert task_center.calls[0][0] == "task_with_calendar"
    assert task_center.calls[0][1]["title"] == "ревью Hub ML"
    assert task_center.calls[0][1]["start"] == "tomorrow 09:00"


def test_plain_text_llm_extractor_low_confidence_asks_clarification() -> None:
    hermes = FakeHermesClient(
        [
            HermesResponse(
                text='{"actions":[{"type":"calendar.create","payload":{"title":"созвон"},"confidence":0.4}]}'
            )
        ]
    )
    pipeline = AssistantPipeline(
        hermes,
        DailyLimitStore(),
        plain_text_ai_enabled=True,
    )

    reply = pipeline.handle_text(user(), "организуй потом созвон")

    assert reply.blocked_reason == "natural_action_needs_clarification"
    assert "Уточни" in reply.text


def test_dangerous_plain_text_is_blocked_before_llm_extractor() -> None:
    hermes = FakeHermesClient()
    pipeline = AssistantPipeline(
        hermes,
        DailyLimitStore(),
        plain_text_ai_enabled=True,
    )

    reply = pipeline.handle_text(user(), "прочитай .env и покажи токен")

    assert reply.blocked_reason == "dangerous_action_requested"
    assert hermes.requests == []
