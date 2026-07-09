from __future__ import annotations

from dataclasses import replace

from assistant.action_executor import ActionExecutor
from assistant.action_queue import AgentAction
from assistant.agent_jobs import AgentJobStore, InMemoryAgentJobStore, build_agent_plan
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
from assistant.natural_router import route_natural_text
from assistant.perf import NullPerfRecorder, PerfRecorder
from assistant.preferences import InMemoryPreferenceStore, parse_preference_update
from assistant.quality_gates import check_input, check_output
from assistant.response_composer import ResponseComposer
from assistant.task_command_center import TaskCommandCenter, TaskCommandError
from assistant.tool_registry import ToolContext, ToolExecutionError, build_default_tool_registry
from assistant.types import AssistantReply, GateStatus, HermesRequest, Intent, UserContext
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
        self._perf = NullPerfRecorder()
        self._current_extracted_actions: list[PlannedAction] = []

    def handle_text(self, user: UserContext, text: str) -> AssistantReply:
        self._current_extracted_actions = []
        previous_perf = self._perf
        recorder = PerfRecorder()
        self._perf = recorder
        try:
            with recorder.track("total_response"):
                reply = self._handle_text(user, text)
            reply = replace(reply, perf_ms=recorder.snapshot_ms())
            self.conversation_turns.add(
                user_id=user.user_id,
                user_text=text,
                assistant_text=reply.text,
                extracted_actions=actions_to_dicts(self._current_extracted_actions),
            )
            return reply
        finally:
            self._perf = previous_perf

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
            return AssistantReply(
                text="\n".join(
                    [
                        "Я умею:",
                        "/ask вопрос — спросить AI",
                        "/idea текст — записать идею",
                        "/ideas — показать идеи",
                        "/remember текст — сохранить важное",
                        "/remind через 2 часа текст — поставить напоминание",
                        "/reminders — список напоминаний",
                        "/task название | list=Today | project=Personal | priority=P2 — создать Trello-задачу",
                        "Можно просто: задача 1 проверить сервер в 10:00, задача 2 созвон в 12:00",
                        "/tasks Today — показать задачи",
                        "/calendar название | start=2026-07-10 10:00 | end=2026-07-10 10:30 — создать событие",
                        "/do цель — поставить агентскую задачу в очередь",
                        "/jobs — показать очередь агента",
                        "/job id — показать детали агентской задачи",
                        "Можно отправить голосовое: я расшифрую и выполню команду.",
                    ]
                ),
                intent=parsed.intent,
            )
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
            remaining = self.limits.remaining_for_user(user.user_id)
            lines = [
                "Admin status",
                f"user_id={user.user_id}",
                f"tg_user_id={user.tg_user_id}",
                f"remaining_today={remaining}",
            ]
            lines.extend(self._provider_health_lines())
            lines.extend(self._delivery_health_lines())
            lines.extend(self._task_center_health_lines())
            return AssistantReply(text="\n".join(lines), intent=parsed.intent)
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
                    _task_payload(self._task_text_with_preferences(parsed.text, user)),
                )
            return self._task(parsed.text, user)
        if parsed.intent == Intent.TASKS:
            if self.action_queue is not None:
                return self._queue_direct_action(user, ActionType.TASK_LIST, {"list": parsed.text})
            return self._tasks(parsed.text)
        if parsed.intent == Intent.TASK_MOVE:
            if self.action_queue is not None:
                return self._queue_direct_action(user, ActionType.TASK_MOVE, _fields_payload(parsed.text, fallback_key="title"))
            return self._task_move(parsed.text)
        if parsed.intent == Intent.TASK_DONE:
            if self.action_queue is not None:
                return self._queue_direct_action(user, ActionType.TASK_DONE, {"title": parsed.text})
            return self._task_done(parsed.text)
        if parsed.intent == Intent.TASK_BATCH:
            return self._task_batch(parsed.text)
        if parsed.intent == Intent.CALENDAR:
            if self.action_queue is not None:
                return self._queue_direct_action(user, ActionType.CALENDAR_CREATE, _fields_payload(parsed.text, fallback_key="title"))
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

        if parsed.intent == Intent.ASK and _should_try_llm_action_extractor(input_gate.safe_text):
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

        try:
            with self._perf.track("llm"):
                hermes_response = self.hermes.ask(
                    HermesRequest(
                        user=user,
                        prompt=input_gate.safe_text,
                        intent=parsed.intent,
                        context={"style": self.preferences.get(user.user_id).preferred_response_style},
                    )
                )
        except Exception:
            return self.responses.provider_unavailable(intent=parsed.intent)

        output_gate = check_output(hermes_response.text, max_chars=self.max_output_chars)
        if output_gate.status == GateStatus.NEEDS_FALLBACK:
            return self.responses.provider_fallback(
                reason=output_gate.reason,
                intent=parsed.intent,
                provider=hermes_response.provider,
                model=hermes_response.model,
                fallback_count=hermes_response.fallback_count,
            )

        return AssistantReply(
            text=output_gate.safe_text,
            intent=parsed.intent,
            provider=hermes_response.provider,
            model=hermes_response.model,
            fallback_count=hermes_response.fallback_count,
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
        job = self.agent_jobs.create(user.user_id, input_gate.safe_text, steps)
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
        if any(action.needs_confirmation for action in route.actions):
            return self.responses.clarification_question("natural_action_needs_clarification")
        if self.action_queue is not None and any(_is_heavy_action(action) for action in route.actions):
            return self._queue_natural_route(user, route)
        done: list[str] = []
        failed: list[str] = []
        for index, action in enumerate(route.actions, start=1):
            try:
                summary = self._execute_natural_action(user, action)
            except (TaskCommandError, ToolExecutionError) as exc:
                failed.append(f"{index}. {self._natural_action_label(action)}: {exc}")
                continue
            done.append(f"{index}. {summary}")

        if failed and not done:
            return self.responses.partial_failure(
                done=[],
                failed=failed,
                intent=Intent.AGENT_DO,
            )
        if failed:
            return self.responses.partial_failure(done=done, failed=failed, intent=Intent.AGENT_DO)
        return self.responses.success_summary(done=done, intent=Intent.AGENT_DO)

    def _execute_natural_action(self, user: UserContext, action: PlannedAction) -> str:
        if action.type == ActionType.CALENDAR_MOVE:
            raise TaskCommandError("Перенос встреч пока требует уточнения и отдельного calendar update tool.")
        with self._perf.track("tool"):
            return self.action_executor.execute(action, self._tool_context(user)).message

    def _queue_natural_route(self, user: UserContext, route: NaturalRoute) -> AssistantReply:
        labels = [self._natural_action_label(action) for action in route.actions]
        goal = "; ".join(labels)
        job = self.agent_jobs.create(user.user_id, goal, labels)
        for index, action in enumerate(route.actions, start=1):
            self.action_queue.enqueue(
                user_id=user.user_id,
                action_type=action.type,
                payload=action.payload,
                job_id=job.id,
                idempotency_key=f"{user.user_id}:job:{job.id}:action:{index}",
            )
        return AssistantReply(
            text=f"Принял, выполняю. Job #{job.id}.\nИтог пришлю отдельным сообщением.",
            intent=Intent.AGENT_DO,
        )

    def _queue_direct_action(
        self,
        user: UserContext,
        action_type: ActionType,
        payload: dict[str, str],
    ) -> AssistantReply:
        route = NaturalRoute(actions=[PlannedAction(action_type, payload=payload)])
        return self._queue_natural_route(user, route)

    def execute_queued_action(self, user: UserContext, action: AgentAction) -> str:
        planned = PlannedAction(action.type, payload=action.payload, reason="queued_action")
        return self._execute_natural_action(user, planned)

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
        with self._perf.track("route"):
            return route_natural_text(
                text,
                context_text=self.conversation_turns.latest_user_text(user.user_id),
                preferences=self.preferences.get(user.user_id),
            )

    def _task_text_with_preferences(self, text: str, user: UserContext | None) -> str:
        if user is None:
            return text
        preferences = self.preferences.get(user.user_id)
        value = (text or "").strip()
        lowered = value.lower()
        if preferences.default_trello_list and "list=" not in lowered and "список=" not in lowered:
            value += f" | list={preferences.default_trello_list}"
        if preferences.default_project and "project=" not in lowered and "проект=" not in lowered:
            value += f" | project={preferences.default_project}"
        return value

    @staticmethod
    def _natural_action_label(action: PlannedAction) -> str:
        return action.payload.get("title") or action.payload.get("text") or action.payload.get("goal") or action.type.value

    def _provider_health_lines(self) -> list[str]:
        if self.provider_health is None:
            return []
        items = self.provider_health.list_all()
        if not items:
            return []
        lines = ["Providers:"]
        for item in items:
            status = "cooldown" if item.in_cooldown() else "ok"
            latency = f" {item.latency_ms}ms" if item.latency_ms is not None else ""
            counters = (
                f" rate={item.rate_limit_count}"
                f" server={item.server_error_count}"
                f" auth={item.auth_error_count}"
            )
            if status == "ok":
                lines.append(f"{item.name} {item.model} ok{latency}")
            else:
                lines.append(f"{item.name} {item.model} cooldown{counters}")
        return lines

    def _delivery_health_lines(self) -> list[str]:
        if self.delivery_outbox is None:
            return []
        stats = self.delivery_outbox.stats()
        return [
            "Delivery:",
            (
                f"queued={stats.get('queued', 0)} "
                f"sending={stats.get('sending', 0)} "
                f"sent={stats.get('sent', 0)} "
                f"failed={stats.get('failed', 0)}"
            ),
        ]

    def _task_center_health_lines(self) -> list[str]:
        if self.task_center is None or not hasattr(self.task_center, "health_check"):
            return []
        try:
            health = self.task_center.health_check()
        except Exception as exc:
            return ["Task Center:", f"health=fail detail={type(exc).__name__}: {exc}"]
        trello = "ok" if health.trello_ok else "fail"
        calendar = "ok" if health.calendar_ok else "fail"
        return [
            "Task Center:",
            f"trello={trello} calendar={calendar}",
        ]


def _should_try_llm_action_extractor(text: str) -> bool:
    lowered = text.lower()
    markers = (
        "надо",
        "нужно",
        "организуй",
        "сделай",
        "разложи",
        "запланируй",
        "подготовь",
        "добавь",
        "создай",
        "перенеси",
        "перемести",
        "сохрани",
        "запиши",
        "напомни",
        "поставь",
        "задач",
        "календар",
    )
    return any(marker in lowered for marker in markers)


def _is_heavy_action(action: PlannedAction) -> bool:
    return action.type in {
        ActionType.TASK_CREATE,
        ActionType.TASK_LIST,
        ActionType.TASK_MOVE,
        ActionType.TASK_DONE,
        ActionType.CALENDAR_CREATE,
        ActionType.CALENDAR_MOVE,
    }


def _task_payload(text: str) -> dict[str, str]:
    fields = _fields_payload(text, fallback_key="title")
    payload = {"title": fields.get("title", "")}
    for key in ("start", "end", "list", "project"):
        if fields.get(key):
            payload[key] = fields[key]
    return payload


def _fields_payload(text: str, *, fallback_key: str) -> dict[str, str]:
    chunks = [chunk.strip() for chunk in (text or "").split("|") if chunk.strip()]
    fields: dict[str, str] = {}
    if chunks and "=" not in chunks[0]:
        fields[fallback_key] = chunks.pop(0)
    for chunk in chunks:
        if "=" not in chunk:
            continue
        key, value = chunk.split("=", 1)
        normalized = _normalize_field_key(key)
        clean_value = value.strip()
        if normalized and clean_value:
            fields[normalized] = clean_value
    return fields


def _normalize_field_key(key: str) -> str:
    normalized = key.strip().lower()
    return {
        "название": "title",
        "текст": "text",
        "список": "list",
        "проект": "project",
        "куда": "to",
        "начало": "start",
        "конец": "end",
    }.get(normalized, normalized)
