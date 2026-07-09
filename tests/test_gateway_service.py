from assistant.hermes_client import FakeHermesClient
from assistant.limits import DailyLimitStore
from assistant.pipeline import AssistantPipeline
from backend.db import init_db, make_session_factory
from backend.stores import (
    EventStore,
    SqlActionQueueStore,
    SqlAgentJobStore,
    SqlDeliveryOutboxStore,
    SqlTraceStore,
    UserStore,
)
from gateway_bot.service import GatewayService


def make_service(*, allowed: set[int] | None = None) -> GatewayService:
    return GatewayService(
        pipeline=AssistantPipeline(FakeHermesClient(), DailyLimitStore()),
        allowed_tg_user_ids=allowed,
    )


def session_factory(tmp_path):
    factory = make_session_factory(f"sqlite:///{tmp_path / 'gateway.sqlite3'}")
    init_db(factory)
    return factory


def test_gateway_service_preserves_memory_between_messages() -> None:
    service = make_service()
    saved = service.handle_text(1001, "/remember важная мысль")
    listed = service.handle_text(1001, "/memories")

    assert "Сохранил" in saved.text
    assert "важная мысль" in listed.text


def test_gateway_service_blocks_user_not_in_allowlist() -> None:
    service = make_service(allowed={1001})
    reply = service.handle_text(2002, "/ask привет")
    assert reply.blocked_reason == "user_not_allowed"
    assert "закрыт" in reply.text


def test_gateway_service_allows_user_in_allowlist() -> None:
    service = make_service(allowed={1001})
    reply = service.handle_text(1001, "/ask привет")
    assert reply.blocked_reason is None
    assert "привет" in reply.text


def test_admin_status_requires_admin() -> None:
    service = make_service()
    reply = service.handle_text(1001, "/admin_status")
    assert reply.blocked_reason == "admin_required"


def test_admin_status_for_admin() -> None:
    service = GatewayService(
        pipeline=AssistantPipeline(FakeHermesClient(), DailyLimitStore()),
        admin_tg_user_ids={1001},
    )
    reply = service.handle_text(1001, "/admin_status")
    assert reply.blocked_reason is None
    assert "Admin status" in reply.text


def test_gateway_confirms_and_cancels_own_actions() -> None:
    from assistant.action_queue import ActionStatus, InMemoryActionQueueStore
    from assistant.action_schema import ActionType
    from assistant.agent_jobs import InMemoryAgentJobStore

    queue = InMemoryActionQueueStore()
    jobs = InMemoryAgentJobStore()
    job = jobs.create(1001, "создать задачу", ["создать задачу"], trace_id="trace-confirm")
    action = queue.enqueue(
        user_id=1001,
        action_type=ActionType.TASK_CREATE,
        payload={"title": "проверить"},
        job_id=job.id,
        trace_id=job.trace_id,
        status=ActionStatus.NEEDS_CONFIRMATION,
    )
    service = GatewayService(
        pipeline=AssistantPipeline(
            FakeHermesClient(),
            DailyLimitStore(),
            agent_jobs=jobs,
            action_queue=queue,
        ),
    )

    status = service.job_status(1001, job.id)
    confirmed = service.confirm_action(1001, action.id)

    assert "needs_confirmation" in status.text
    assert confirmed.trace_id == "trace-confirm"
    assert queue.claim_next().id == action.id


def test_gateway_confirms_whole_job_with_single_button() -> None:
    from assistant.action_queue import ActionStatus, InMemoryActionQueueStore
    from assistant.action_schema import ActionType
    from assistant.agent_jobs import InMemoryAgentJobStore

    queue = InMemoryActionQueueStore()
    jobs = InMemoryAgentJobStore()
    job = jobs.create(1001, "создать две вещи", ["task", "calendar"], trace_id="trace-job-confirm")
    first = queue.enqueue(
        user_id=1001,
        action_type=ActionType.TASK_CREATE,
        payload={"title": "проверить"},
        job_id=job.id,
        trace_id=job.trace_id,
        status=ActionStatus.NEEDS_CONFIRMATION,
    )
    second = queue.enqueue(
        user_id=1001,
        action_type=ActionType.CALENDAR_CREATE,
        payload={"title": "созвон", "start": "tomorrow 10:00", "end": "tomorrow 10:30"},
        job_id=job.id,
        trace_id=job.trace_id,
        status=ActionStatus.NEEDS_CONFIRMATION,
        depends_on_action_id=first.id,
    )
    service = GatewayService(
        pipeline=AssistantPipeline(
            FakeHermesClient(),
            DailyLimitStore(),
            agent_jobs=jobs,
            action_queue=queue,
        ),
    )

    status = service.job_status(1001, job.id)
    confirmed = service.confirm_job(1001, job.id)

    assert status.buttons[0][0].callback_data == f"ai:confirm_job:{job.id}"
    assert status.buttons[0][1].callback_data == f"ai:cancel_job:{job.id}"
    assert "2 действий" in confirmed.text
    assert queue.claim_next().id == first.id
    queue.mark_succeeded(first.id)
    assert queue.claim_next().id == second.id


def test_gateway_cancels_whole_job_with_single_button() -> None:
    from assistant.action_queue import ActionStatus, InMemoryActionQueueStore
    from assistant.action_schema import ActionType
    from assistant.agent_jobs import InMemoryAgentJobStore

    queue = InMemoryActionQueueStore()
    jobs = InMemoryAgentJobStore()
    job = jobs.create(1001, "отменить всё", ["task", "calendar"], trace_id="trace-job-cancel")
    queue.enqueue(
        user_id=1001,
        action_type=ActionType.TASK_CREATE,
        payload={"title": "проверить"},
        job_id=job.id,
        trace_id=job.trace_id,
        status=ActionStatus.NEEDS_CONFIRMATION,
    )
    queue.enqueue(
        user_id=1001,
        action_type=ActionType.CALENDAR_CREATE,
        payload={"title": "созвон", "start": "tomorrow 10:00", "end": "tomorrow 10:30"},
        job_id=job.id,
        trace_id=job.trace_id,
        status=ActionStatus.NEEDS_CONFIRMATION,
    )
    service = GatewayService(
        pipeline=AssistantPipeline(
            FakeHermesClient(),
            DailyLimitStore(),
            agent_jobs=jobs,
            action_queue=queue,
        ),
    )

    cancelled = service.cancel_job(1001, job.id)

    assert "2 действий" in cancelled.text
    assert queue.claim_next() is None
    assert "cancelled" in service.job_status(1001, job.id).text


def test_telegram_callback_routes_job_level_confirmation() -> None:
    from gateway_bot.telegram_callbacks import handle_callback_data

    class FakeService:
        def __init__(self) -> None:
            self.calls = []

        def confirm_job(self, tg_user_id: int, job_id: int):
            self.calls.append(("confirm_job", tg_user_id, job_id))
            return "confirmed"

        def cancel_job(self, tg_user_id: int, job_id: int):
            self.calls.append(("cancel_job", tg_user_id, job_id))
            return "cancelled"

    service = FakeService()

    assert handle_callback_data(service, 1001, "ai:confirm_job:7") == "confirmed"
    assert handle_callback_data(service, 1001, "ai:cancel_job:7") == "cancelled"
    assert service.calls == [("confirm_job", 1001, 7), ("cancel_job", 1001, 7)]


def test_gateway_job_status_shows_dependencies_progress_and_compensation() -> None:
    from assistant.action_queue import ActionStatus, InMemoryActionQueueStore
    from assistant.action_schema import ActionType
    from assistant.agent_jobs import InMemoryAgentJobStore

    queue = InMemoryActionQueueStore()
    jobs = InMemoryAgentJobStore()
    job = jobs.create(1001, "сделать цепочку", ["сохранить", "создать задачу"], trace_id="trace-chain")
    first = queue.enqueue(
        user_id=1001,
        action_type=ActionType.IDEA_SAVE,
        payload={"text": "сохранить"},
        job_id=job.id,
        trace_id=job.trace_id,
    )
    second = queue.enqueue(
        user_id=1001,
        action_type=ActionType.TASK_CREATE,
        payload={"title": "создать задачу"},
        job_id=job.id,
        trace_id=job.trace_id,
        depends_on_action_id=first.id,
    )
    queue.mark_succeeded(first.id)
    queue.mark_failed(second.id, "task failed")
    queue.mark_compensation_skipped_for_job(job.id, second.id, "manual rollback required")
    service = GatewayService(
        pipeline=AssistantPipeline(
            FakeHermesClient(),
            DailyLimitStore(),
            agent_jobs=jobs,
            action_queue=queue,
        ),
    )

    reply = service.job_status(1001, job.id)

    assert "computed=partial_failure" in reply.text
    assert "Прогресс: 1/2" in reply.text
    assert f"{second.id}. task.create — failed after #{first.id}" in reply.text
    assert "compensation=not_supported" in reply.text


def test_gateway_cancels_own_action_with_trace() -> None:
    from assistant.action_queue import ActionStatus, InMemoryActionQueueStore
    from assistant.action_schema import ActionType
    from assistant.agent_jobs import InMemoryAgentJobStore

    queue = InMemoryActionQueueStore()
    jobs = InMemoryAgentJobStore()
    job = jobs.create(1001, "создать встречу", ["создать встречу"], trace_id="trace-cancel")
    action = queue.enqueue(
        user_id=1001,
        action_type=ActionType.CALENDAR_CREATE,
        payload={"title": "созвон"},
        job_id=job.id,
        trace_id=job.trace_id,
        status=ActionStatus.NEEDS_CONFIRMATION,
    )
    service = GatewayService(
        pipeline=AssistantPipeline(
            FakeHermesClient(),
            DailyLimitStore(),
            agent_jobs=jobs,
            action_queue=queue,
        ),
    )

    cancelled = service.cancel_action(1001, action.id)

    assert cancelled.trace_id == "trace-cancel"
    assert "Отменил" in cancelled.text


def test_trace_command_requires_admin(tmp_path) -> None:
    factory = session_factory(tmp_path)
    service = GatewayService(
        pipeline=AssistantPipeline(FakeHermesClient(), DailyLimitStore()),
        users=UserStore(factory),
        traces=SqlTraceStore(factory),
    )

    reply = service.handle_text(1001, "/trace trace-1")

    assert reply.blocked_reason == "admin_required"


def test_trace_command_shows_job_action_delivery_and_events(tmp_path) -> None:
    from assistant.action_schema import ActionType

    factory = session_factory(tmp_path)
    user = UserStore(factory).get_or_create(1001)
    trace_id = "trace-view-1"
    job = SqlAgentJobStore(factory).create(user.id, "создать задачу", ["создать задачу"], trace_id=trace_id)
    action = SqlActionQueueStore(factory).enqueue(
        user_id=user.id,
        action_type=ActionType.TASK_CREATE,
        payload={"title": "проверить trace"},
        job_id=job.id,
        trace_id=trace_id,
    )
    SqlDeliveryOutboxStore(factory).enqueue(user_id=user.id, chat_id=user.tg_user_id, text="готово", trace_id=trace_id)
    EventStore(factory).log(
        user.id,
        "action_started",
        {"job_id": job.id, "action_id": action.id, "type": action.type.value},
        trace_id=trace_id,
    )
    service = GatewayService(
        pipeline=AssistantPipeline(FakeHermesClient(), DailyLimitStore()),
        admin_tg_user_ids={1001},
        users=UserStore(factory),
        traces=SqlTraceStore(factory),
    )

    reply = service.handle_text(1001, f"/trace {trace_id}")

    assert reply.blocked_reason is None
    assert f"Trace {trace_id}" in reply.text
    assert "Jobs:" in reply.text
    assert "Actions:" in reply.text
    assert "Delivery:" in reply.text
    assert "Events:" in reply.text
    assert "action_started" in reply.text


def test_trace_command_handles_missing_trace(tmp_path) -> None:
    factory = session_factory(tmp_path)
    service = GatewayService(
        pipeline=AssistantPipeline(FakeHermesClient(), DailyLimitStore()),
        admin_tg_user_ids={1001},
        users=UserStore(factory),
        traces=SqlTraceStore(factory),
    )

    reply = service.handle_text(1001, "/trace missing")

    assert reply.blocked_reason == "trace_not_found"
    assert "ничего не найдено" in reply.text


def test_telegram_app_imports_without_aiogram_runtime() -> None:
    import gateway_bot.telegram_app as telegram_app
    import gateway_bot.telegram_callbacks as telegram_callbacks
    import gateway_bot.telegram_handlers as telegram_handlers
    import gateway_bot.telegram_workers as telegram_workers

    assert telegram_app.START_TEXT
    assert telegram_app.create_dispatcher is telegram_handlers.create_dispatcher
    assert telegram_app.start_background_workers is telegram_workers.start_background_workers
    assert telegram_callbacks.handle_callback_data


def test_handle_local_text_preserves_process_state() -> None:
    import gateway_bot.main as gateway_main

    gateway_main._gateway_service = None
    assert "Сохранил" in gateway_main.handle_local_text(3003, "/remember локальная память")
    assert "локальная память" in gateway_main.handle_local_text(3003, "/memories")


def test_handle_local_plain_text_goes_to_ai_by_default(tmp_path) -> None:
    import gateway_bot.main as gateway_main

    gateway_main._gateway_service = None
    gateway_main._session_factory = None
    object.__setattr__(gateway_main.settings, "database_url", f"sqlite:///{tmp_path / 'gateway.sqlite3'}")
    object.__setattr__(gateway_main.settings, "hermes_mode", "fake")
    reply = gateway_main.handle_local_text(3004, "объясни MVP")
    assert "объясни MVP" in reply
