from __future__ import annotations

from dataclasses import replace

from assistant.action_executor import ActionExecutor
from assistant.action_queue import AgentAction
from assistant.agent_jobs import AgentJobStore, InMemoryAgentJobStore, build_agent_plan
from assistant.admin_status_service import build_admin_status_text
from assistant.ai_answer_service import answer_with_ai
from assistant.command_handlers import (
    fields_payload,
    help_text,
    should_try_llm_action_extractor,
    task_payload,
    task_text_with_preferences,
)
from assistant.context_store import InMemoryConversationStore, actions_to_dicts
from assistant.hermes_client import HermesClient
from assistant.google_docs_sync import DocsSync, NullDocsSync
from assistant.ideas import InMemoryIdeaStore
from assistant.intents import parse_message
from assistant.limits import DailyLimitStore
from assistant.memory import InMemoryMemoryStore
from assistant.natural_tasks import parse_natural_task_batch
from assistant.action_schema import ActionType, NaturalRoute, PlannedAction
from assistant.llm_action_extractor import LlmActionExtractor
from assistant.natural_action_service import NaturalActionService
from assistant.perf import NullPerfRecorder, PerfRecorder
from assistant.preferences import InMemoryPreferenceStore, parse_preference_update
from assistant.quality_gates import check_input
from assistant.response_composer import ResponseComposer
from assistant.task_command_center import TaskCommandCenter, TaskCommandError
from assistant.tool_registry import ToolContext, build_default_tool_registry
from assistant.tracing import new_trace_id
from assistant.types import AssistantReply, Intent, UserContext
from reminders.parser import parse_reminder
from reminders.store import InMemoryReminderStore


class AssistantPipeline:
    def __init__(
        self,
        hermes: HermesClient,
        limits: DailyLimitStore,
        *,
        plain_text_ai_enabled: bool = False,
        max_input_chars: int = 4000,
        max_output_chars: int = 2500,
        memories: InMemoryMemoryStore | None = None,
        ideas: InMemoryIdeaStore | None = None,
        reminders: InMemoryReminderStore | None = None,
        docs_sync: DocsSync | None = None,
        task_center: TaskCommandCenter | None = None,
        agent_jobs: AgentJobStore | None = None,
        action_extractor: LlmActionExtractor | None = None,
        action_executor: ActionExecutor | None = None,
        conversation_turns: InMemoryConversationStore | None = None,
        preferences: InMemoryPreferenceStore | None = None,
        response_composer: ResponseComposer | None = None,
        provider_health=None,
        delivery_outbox=None,
        action_queue=None,
        events=None,
    ) -> None:
        self.hermes = hermes
        self.limits = limits
        self.plain_text_ai_enabled = plain_text_ai_enabled
        self.max_input_chars = max_input_chars
        self.max_output_chars = max_output_chars
        self.memories = memories or InMemoryMemoryStore()
        self.ideas = ideas or InMemoryIdeaStore()
        self.reminders = reminders or InMemoryReminderStore()
        self.docs_sync = docs_sync or NullDocsSync()
        self.task_center = task_center
        self.agent_jobs = agent_jobs or InMemoryAgentJobStore()
        self.action_extractor = action_extractor or LlmActionExtractor(hermes)
        self.action_executor = action_executor or ActionExecutor(build_default_tool_registry())
        self.conversation_turns = conversation_turns or InMemoryConversationStore()
        self.preferences = preferences or InMemoryPreferenceStore()
        self.responses = response_composer or ResponseComposer()
        self.provider_health = provider_health
        self.delivery_outbox = delivery_outbox
        self.action_queue = action_queue
        self.events = events
        self.natural_actions = NaturalActionService(
            action_executor=self.action_executor,
            agent_jobs=self.agent_jobs,
            responses=self.responses,
            tool_context_factory=self._tool_context,
            action_queue=self.action_queue,
            events=self.events,
        )
        self._perf = NullPerfRecorder()
        self._current_trace_id = ""
        self._current_extracted_actions: list[PlannedAction] = []

    def handle_text(self, user: UserContext, text: str) -> AssistantReply:
        self._current_extracted_actions = []
        previous_perf = self._perf
        previous_trace_id = self._current_trace_id
        recorder = PerfRecorder()
        trace_id = new_trace_id()
        self._perf = recorder
        self._current_trace_id = trace_id
        try:
            with recorder.track("total_response"):
                reply = self._handle_text(user, text)
            reply = replace(reply, perf_ms=recorder.snapshot_ms(), trace_id=reply.trace_id or trace_id)
            self.conversation_turns.add(
                user_id=user.user_id,
                user_text=text,
                assistant_text=reply.text,
                extracted_actions=actions_to_dicts(self._current_extracted_actions),
            )
            return reply
        finally:
            self._perf = previous_perf
            self._current_trace_id = previous_trace_id

    def _handle_text(self, user: UserContext, text: str) -> AssistantReply:
        with self._perf.track("intent_parse"):
            preference_update = parse_preference_update(text)
            if preference_update is not None:
                self.preferences.update(user.user_id, **preference_update.updates)
                return AssistantReply(text=preference_update.message, intent=Intent.STATUS)

            parsed = parse_message(text, plain_text_ai_enabled=self.plain_text_ai_enabled)
        natural_route = None
        if not (parsed.raw_text or "").strip().startswith("/"):
            natural_route = self._route_natural_text(user, parsed.raw_text)
            if len(natural_route.actions) > 1:
                return self._execute_natural_route(user, natural_route)

        if parsed.intent == Intent.HELP:
            return AssistantReply(text=help_text(), intent=parsed.intent)
        if parsed.intent == Intent.STATUS:
            remaining = self.limits.remaining_for_user(user.user_id)
            return AssistantReply(text=f"AI включён. Осталось запросов сегодня: {remaining}.", intent=parsed.intent)
        if parsed.intent == Intent.ADMIN_STATUS:
            if not user.is_admin:
                return AssistantReply(
                    text="Эта команда доступна только владельцу бота.",
                    intent=parsed.intent,
                    blocked_reason="admin_required",
                )
            return AssistantReply(
                text=build_admin_status_text(
                    user=user,
                    limits=self.limits,
                    provider_health=self.provider_health,
                    delivery_outbox=self.delivery_outbox,
                    task_center=self.task_center,
                ),
                intent=parsed.intent,
            )
        if parsed.intent == Intent.UNKNOWN:
            natural_route = natural_route or self._route_natural_text(user, parsed.raw_text)
            if natural_route.actions:
                return self._execute_natural_route(user, natural_route)
            return AssistantReply(
                text="Пока я отвечаю на AI-вопросы через /ask. Например: /ask объясни идею MVP простыми словами.",
                intent=parsed.intent,
            )
        if parsed.intent == Intent.REMEMBER:
            return self._remember(user, parsed.text)
        if parsed.intent == Intent.MEMORIES:
            return self._memories(user)
        if parsed.intent == Intent.IDEA:
            return self._idea(user, parsed.text)
        if parsed.intent == Intent.IDEAS:
            return self._ideas(user)
        if parsed.intent == Intent.REMIND:
            return self._remind(user, parsed.text)
        if parsed.intent == Intent.REMINDERS:
            return self._reminders(user)
        if parsed.intent == Intent.CANCEL_REMINDER:
            return self._cancel_reminder(user, parsed.text)
        if parsed.intent == Intent.TASK:
            if self.action_queue is not None:
                return self._queue_direct_action(
                    user,
                    ActionType.TASK_CREATE,
                    task_payload(self._task_text_with_preferences(parsed.text, user)),
                )
            return self._task(parsed.text, user)
        if parsed.intent == Intent.TASKS:
            if self.action_queue is not None:
                return self._queue_direct_action(user, ActionType.TASK_LIST, {"list": parsed.text})
            return self._tasks(parsed.text)
        if parsed.intent == Intent.TASK_MOVE:
            if self.action_queue is not None:
                return self._queue_direct_action(user, ActionType.TASK_MOVE, fields_payload(parsed.text, fallback_key="title"))
            return self._task_move(parsed.text)
        if parsed.intent == Intent.TASK_DONE:
            if self.action_queue is not None:
                return self._queue_direct_action(user, ActionType.TASK_DONE, {"title": parsed.text})
            return self._task_done(parsed.text)
        if parsed.intent == Intent.TASK_BATCH:
            return self._task_batch(parsed.text)
        if parsed.intent == Intent.CALENDAR:
            if self.action_queue is not None:
                return self._queue_direct_action(user, ActionType.CALENDAR_CREATE, fields_payload(parsed.text, fallback_key="title"))
            return self._calendar(parsed.text)
        if parsed.intent == Intent.AGENT_DO:
            return self._agent_do(user, parsed.text)
        if parsed.intent == Intent.AGENT_JOBS:
            return self._agent_jobs(user)
        if parsed.intent == Intent.AGENT_JOB:
            return self._agent_job(user, parsed.text)

        if parsed.intent in {Intent.ASK, Intent.UNKNOWN} and not (parsed.raw_text or "").strip().startswith("/"):
            natural_route = natural_route or self._route_natural_text(user, parsed.raw_text)
            if natural_route.actions:
                return self._execute_natural_route(user, natural_route)

        input_gate = check_input(parsed.text, max_chars=self.max_input_chars)
        if not input_gate.ok:
            return self.responses.blocked_action(input_gate.reason, intent=parsed.intent)

        if parsed.intent == Intent.ASK and should_try_llm_action_extractor(input_gate.safe_text):
            if not self.limits.consume(user.user_id):
                return self.responses.daily_limit(intent=parsed.intent)
            with self._perf.track("llm"):
                extracted_route = self.action_extractor.extract(user, input_gate.safe_text)
            if extracted_route.actions:
                return self._execute_natural_route(user, extracted_route)
            if not extracted_route.fallback_to_ai:
                return self.responses.clarification_question("natural_action_needs_clarification")
            return AssistantReply(
                text="Не понял, какое действие нужно выполнить. Напиши чуть конкретнее или задай вопрос обычным текстом.",
                intent=Intent.AGENT_DO,
                blocked_reason=extracted_route.reason or "natural_action_parse_failed",
            )

        if not self.limits.consume(user.user_id):
            return self.responses.daily_limit(intent=parsed.intent)

        return answer_with_ai(
            hermes=self.hermes,
            responses=self.responses,
            user=user,
            prompt=input_gate.safe_text,
            intent=parsed.intent,
            style=self.preferences.get(user.user_id).preferred_response_style,
            max_output_chars=self.max_output_chars,
            perf=self._perf,
            trace_id=self._current_trace_id,
            events=self.events,
        )

    def _remember(self, user: UserContext, text: str) -> AssistantReply:
        input_gate = check_input(text, max_chars=1000)
        if not input_gate.ok:
            return self.responses.blocked_action(input_gate.reason, intent=Intent.REMEMBER)
        with self._perf.track("tool"):
            item = self.memories.add(user.user_id, input_gate.safe_text)
        return AssistantReply(text=f"Сохранил важное #{item.id}.", intent=Intent.REMEMBER)

    def _memories(self, user: UserContext) -> AssistantReply:
        items = self.memories.list_for_user(user.user_id)
        if not items:
            return AssistantReply(text="Пока нет сохранённых заметок.", intent=Intent.MEMORIES)
        lines = [f"{item.id}. {item.text}" for item in items]
        return AssistantReply(text="Сохранённое:\n" + "\n".join(lines), intent=Intent.MEMORIES)

    def _idea(self, user: UserContext, text: str) -> AssistantReply:
        input_gate = check_input(text, max_chars=1000)
        if not input_gate.ok:
            return self.responses.blocked_action(input_gate.reason, intent=Intent.IDEA)
        with self._perf.track("tool"):
            item = self.ideas.add(user.user_id, input_gate.safe_text)
            synced = self.docs_sync.append(
                kind="idea",
                user_id=user.user_id,
                text=item.text,
                created_at=item.created_at,
                record_id=str(item.id),
            )
        suffix = " Отправил в Google Docs." if synced else ""
        return AssistantReply(text=f"Сохранил идею #{item.id}.{suffix}", intent=Intent.IDEA)

    def _ideas(self, user: UserContext) -> AssistantReply:
        items = self.ideas.list_for_user(user.user_id)
        if not items:
            return AssistantReply(text="Пока нет идей.", intent=Intent.IDEAS)
        lines = [f"{item.id}. {item.text}" for item in items]
        return AssistantReply(text="Идеи:\n" + "\n".join(lines), intent=Intent.IDEAS)

    def _remind(self, user: UserContext, text: str) -> AssistantReply:
        parsed = parse_reminder(text, default_time=self.preferences.get(user.user_id).default_reminder_time)
        if parsed is None:
            return AssistantReply(
                text="Не понял время. Сейчас поддерживаю формат: /remind через 2 часа текст или /remind 2026-07-09 09:30 текст.",
                intent=Intent.REMIND,
                blocked_reason="reminder_parse_failed",
            )
        input_gate = check_input(parsed.text, max_chars=1000)
        if not input_gate.ok:
            return self.responses.blocked_action(input_gate.reason, intent=Intent.REMIND)
        with self._perf.track("tool"):
            item = self.reminders.add(user.user_id, input_gate.safe_text, parsed.remind_at)
            synced = self.docs_sync.append(
                kind="reminder",
                user_id=user.user_id,
                text=f"{item.remind_at.isoformat()} — {item.text}",
                created_at=item.remind_at,
                record_id=str(item.id),
            )
        suffix = " Отправил в Google Docs." if synced else ""
        return AssistantReply(
            text=f"Поставил напоминание #{item.id}: {item.remind_at.isoformat()} — {item.text}{suffix}",
            intent=Intent.REMIND,
        )

    def _reminders(self, user: UserContext) -> AssistantReply:
        items = self.reminders.list_pending_for_user(user.user_id)
        if not items:
            return AssistantReply(text="Активных напоминаний нет.", intent=Intent.REMINDERS)
        lines = [f"{item.id}. {item.remind_at.isoformat()} — {item.text}" for item in items]
        return AssistantReply(text="Активные напоминания:\n" + "\n".join(lines), intent=Intent.REMINDERS)

    def _cancel_reminder(self, user: UserContext, text: str) -> AssistantReply:
        value = (text or "").strip()
        if not value.isdigit():
            return AssistantReply(
                text="Укажи номер напоминания: /cancel_reminder 1",
                intent=Intent.CANCEL_REMINDER,
                blocked_reason="cancel_reminder_bad_id",
            )
        reminder_id = int(value)
        cancel = getattr(self.reminders, "cancel_for_user", None)
        with self._perf.track("tool"):
            cancelled = cancel is not None and cancel(user.user_id, reminder_id)
        if not cancelled:
            return AssistantReply(
                text=f"Не нашёл активное напоминание #{reminder_id}.",
                intent=Intent.CANCEL_REMINDER,
                blocked_reason="cancel_reminder_not_found",
            )
        return AssistantReply(text=f"Отменил напоминание #{reminder_id}.", intent=Intent.CANCEL_REMINDER)

    def _task(self, text: str, user: UserContext | None = None) -> AssistantReply:
        if self.task_center is None:
            return AssistantReply(
                text="Task Command Center не подключён. Проверь TASK_COMMAND_CENTER_ENABLED и путь в .env.",
                intent=Intent.TASK,
                blocked_reason="task_center_disabled",
            )
        try:
            with self._perf.track("tool"):
                output = self.task_center.create_task(self._task_text_with_preferences(text, user))
        except TaskCommandError as exc:
            return AssistantReply(text=f"Не создал задачу: {exc}", intent=Intent.TASK, blocked_reason="task_command_failed")
        return AssistantReply(text="Создал задачу:\n" + output, intent=Intent.TASK)

    def _tasks(self, text: str) -> AssistantReply:
        if self.task_center is None:
            return AssistantReply(
                text="Task Command Center не подключён. Проверь TASK_COMMAND_CENTER_ENABLED и путь в .env.",
                intent=Intent.TASKS,
                blocked_reason="task_center_disabled",
            )
        try:
            with self._perf.track("tool"):
                output = self.task_center.list_tasks(text)
        except TaskCommandError as exc:
            return AssistantReply(text=f"Не получил задачи: {exc}", intent=Intent.TASKS, blocked_reason="task_command_failed")
        return AssistantReply(text="Задачи:\n" + output, intent=Intent.TASKS)

    def _task_move(self, text: str) -> AssistantReply:
        if self.task_center is None:
            return AssistantReply(
                text="Task Command Center не подключён. Проверь TASK_COMMAND_CENTER_ENABLED и путь в .env.",
                intent=Intent.TASK_MOVE,
                blocked_reason="task_center_disabled",
            )
        try:
            with self._perf.track("tool"):
                output = self.task_center.move_task(text)
        except TaskCommandError as exc:
            return AssistantReply(text=f"Не переместил задачу: {exc}", intent=Intent.TASK_MOVE, blocked_reason="task_command_failed")
        return AssistantReply(text="Переместил задачу:\n" + output, intent=Intent.TASK_MOVE)

    def _task_done(self, text: str) -> AssistantReply:
        if self.task_center is None:
            return AssistantReply(
                text="Task Command Center не подключён. Проверь TASK_COMMAND_CENTER_ENABLED и путь в .env.",
                intent=Intent.TASK_DONE,
                blocked_reason="task_center_disabled",
            )
        try:
            with self._perf.track("tool"):
                output = self.task_center.complete_task(text)
        except TaskCommandError as exc:
            return AssistantReply(text=f"Не закрыл задачу: {exc}", intent=Intent.TASK_DONE, blocked_reason="task_command_failed")
        return AssistantReply(text="Закрыл задачу:\n" + output, intent=Intent.TASK_DONE)

    def _task_batch(self, text: str) -> AssistantReply:
        if self.task_center is None:
            return AssistantReply(
                text="Task Command Center не подключён. Проверь TASK_COMMAND_CENTER_ENABLED и путь в .env.",
                intent=Intent.TASK_BATCH,
                blocked_reason="task_center_disabled",
            )
        tasks = parse_natural_task_batch(text)
        if not tasks:
            return AssistantReply(
                text="Не понял список задач. Пример: задача 1 проверить Trello в 10:00, задача 2 созвон в 12:00.",
                intent=Intent.TASK_BATCH,
                blocked_reason="task_batch_parse_failed",
            )
        if len(tasks) > 8:
            return AssistantReply(
                text="За один раз могу создать до 8 задач. Разбей список на несколько сообщений.",
                intent=Intent.TASK_BATCH,
                blocked_reason="task_batch_too_large",
            )

        created: list[str] = []
        failed: list[str] = []
        for index, task in enumerate(tasks, start=1):
            try:
                with self._perf.track("tool"):
                    self.task_center.create_task_with_calendar(
                        title=task.title,
                        start=task.start,
                        end=task.end,
                    )
            except TaskCommandError as exc:
                failed.append(f"{index}. {task.title}: {exc}")
                continue
            when = f" ({task.start})" if task.start else ""
            created.append(f"{index}. {task.title}{when}")

        if failed and not created:
            return AssistantReply(
                text="Не создал задачи:\n" + "\n".join(failed),
                intent=Intent.TASK_BATCH,
                blocked_reason="task_batch_failed",
            )
        parts = []
        if created:
            parts.append("Создал задачи:\n" + "\n".join(created))
        if failed:
            parts.append("Не создал:\n" + "\n".join(failed))
        return AssistantReply(text="\n\n".join(parts), intent=Intent.TASK_BATCH)

    def _calendar(self, text: str) -> AssistantReply:
        if self.task_center is None:
            return AssistantReply(
                text="Task Command Center не подключён. Проверь TASK_COMMAND_CENTER_ENABLED и путь в .env.",
                intent=Intent.CALENDAR,
                blocked_reason="task_center_disabled",
            )
        try:
            with self._perf.track("tool"):
                output = self.task_center.create_calendar_event(text)
        except TaskCommandError as exc:
            return AssistantReply(text=f"Не создал событие: {exc}", intent=Intent.CALENDAR, blocked_reason="task_command_failed")
        return AssistantReply(text="Создал событие:\n" + output, intent=Intent.CALENDAR)

    def _agent_do(self, user: UserContext, text: str) -> AssistantReply:
        input_gate = check_input(text, max_chars=1500)
        if not input_gate.ok:
            return self.responses.blocked_action(input_gate.reason, intent=Intent.AGENT_DO)
        steps = build_agent_plan(input_gate.safe_text)
        if not steps:
            return AssistantReply(
                text="Напиши цель после /do. Например: /do разложи завтра задачи по Trello и календарю",
                intent=Intent.AGENT_DO,
                blocked_reason="agent_goal_empty",
            )
        job = self.agent_jobs.create(user.user_id, input_gate.safe_text, steps, trace_id=self._current_trace_id)
        if self.events is not None:
            self.events.log(
                user.user_id,
                "job_created",
                {"job_id": job.id, "goal": job.goal},
                trace_id=self._current_trace_id,
            )
        lines = [
            f"Поставил в очередь job #{job.id}.",
            f"Статус: {job.status}",
            "План:",
        ]
        lines.extend(f"{index}. {step}" for index, step in enumerate(job.steps, start=1))
        lines.append(f"Проверить: /job {job.id}")
        return AssistantReply(text="\n".join(lines), intent=Intent.AGENT_DO)

    def _agent_jobs(self, user: UserContext) -> AssistantReply:
        jobs = self.agent_jobs.list_for_user(user.user_id)
        if not jobs:
            return AssistantReply(text="Очередь агента пустая.", intent=Intent.AGENT_JOBS)
        lines = [
            f"{job.id}. {job.status} — {job.goal[:80]}{'…' if len(job.goal) > 80 else ''}"
            for job in jobs
        ]
        return AssistantReply(text="Очередь агента:\n" + "\n".join(lines), intent=Intent.AGENT_JOBS)

    def _agent_job(self, user: UserContext, text: str) -> AssistantReply:
        value = (text or "").strip()
        if not value.isdigit():
            return AssistantReply(
                text="Укажи номер job: /job 1",
                intent=Intent.AGENT_JOB,
                blocked_reason="agent_job_bad_id",
            )
        job = self.agent_jobs.get_for_user(user.user_id, int(value))
        if job is None:
            return AssistantReply(
                text=f"Не нашёл job #{value}.",
                intent=Intent.AGENT_JOB,
                blocked_reason="agent_job_not_found",
            )
        lines = [
            f"Job #{job.id}",
            f"Статус: {job.status}",
            f"Цель: {job.goal}",
            "Шаги:",
        ]
        lines.extend(f"{index}. {step}" for index, step in enumerate(job.steps, start=1))
        if job.error:
            lines.append(f"Ошибка: {job.error}")
        return AssistantReply(text="\n".join(lines), intent=Intent.AGENT_JOB)

    def _execute_natural_route(self, user: UserContext, route: NaturalRoute) -> AssistantReply:
        self._current_extracted_actions = list(route.actions)
        return self.natural_actions.execute_route(user, route, perf=self._perf, trace_id=self._current_trace_id)

    def _queue_direct_action(
        self,
        user: UserContext,
        action_type: ActionType,
        payload: dict[str, str],
    ) -> AssistantReply:
        return self.natural_actions.queue_direct_action(user, action_type, payload, trace_id=self._current_trace_id)

    def execute_queued_action(self, user: UserContext, action: AgentAction) -> str:
        return self.natural_actions.execute_queued_action(user, action, perf=self._perf, trace_id=action.trace_id)

    def _tool_context(self, user: UserContext) -> ToolContext:
        return ToolContext(
            user=user,
            memories=self.memories,
            ideas=self.ideas,
            reminders=self.reminders,
            docs_sync=self.docs_sync,
            task_center=self.task_center,
            agent_jobs=self.agent_jobs,
            preferences=self.preferences.get(user.user_id),
        )

    def _route_natural_text(self, user: UserContext, text: str) -> NaturalRoute:
        return self.natural_actions.route_text(
            user=user,
            text=text,
            conversation_turns=self.conversation_turns,
            preferences=self.preferences,
            perf=self._perf,
        )

    def _task_text_with_preferences(self, text: str, user: UserContext | None) -> str:
        preferences = self.preferences.get(user.user_id) if user is not None else None
        return task_text_with_preferences(text, preferences)
