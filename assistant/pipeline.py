from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from urllib.parse import urlparse

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
from assistant.communication_style import CommunicationStyleGuide, load_communication_style
from assistant.contact_book import InMemoryContactBookStore
from assistant.context_store import ConversationTurn, InMemoryConversationStore, actions_to_dicts
from assistant.google_docs_sync import DocsSync, NullDocsSync
from assistant.ideas import InMemoryIdeaStore
from assistant.intents import parse_message
from assistant.limits import DailyLimitStore
from assistant.memory import InMemoryMemoryStore
from assistant.natural_tasks import parse_natural_task_batch
from assistant.personal_knowledge import InMemoryPersonalKnowledgeStore, Note
from assistant.action_schema import ActionType, NaturalRoute, PlannedAction
from assistant.llm_action_extractor import LlmActionExtractor
from assistant.natural_action_service import NaturalActionService
from assistant.perf import NullPerfRecorder, PerfRecorder
from assistant.preferences import InMemoryPreferenceStore, parse_preference_update
from assistant.provider_clients import HermesClient
from assistant.quality_gates import check_input
from assistant.response_composer import ResponseComposer
from assistant.task_command_center import TaskCommandCenter, TaskCommandError
from assistant.tool_registry import ToolContext, ToolExecutionResult, _format_reminder_reply, build_default_tool_registry
from assistant.tracing import new_trace_id
from assistant.types import AssistantReply, Intent, UserContext
from reminders.parser import parse_reminder
from reminders.store import InMemoryReminderStore


@dataclass
class _PipelineRequestContext:
    perf: PerfRecorder
    trace_id: str
    idempotency_key: str = ""
    extracted_actions: list[PlannedAction] = field(default_factory=list)


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
        knowledge: InMemoryPersonalKnowledgeStore | None = None,
        contact_book: InMemoryContactBookStore | None = None,
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
        monitor_jobs=None,
        worker_leases=None,
        communication_style: CommunicationStyleGuide | None = None,
    ) -> None:
        self.hermes = hermes
        self.limits = limits
        self.plain_text_ai_enabled = plain_text_ai_enabled
        self.max_input_chars = max_input_chars
        self.max_output_chars = max_output_chars
        self.memories = memories or InMemoryMemoryStore()
        self.ideas = ideas or InMemoryIdeaStore()
        self._explicit_memories = memories
        self._explicit_ideas = ideas
        self.knowledge = knowledge or _knowledge_from_legacy(self.memories, self.ideas) or InMemoryPersonalKnowledgeStore()
        self.contact_book = contact_book or InMemoryContactBookStore()
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
        self.monitor_jobs = monitor_jobs
        self.worker_leases = worker_leases
        self.communication_style = communication_style or load_communication_style(enabled=True)
        self.natural_actions = NaturalActionService(
            action_executor=self.action_executor,
            agent_jobs=self.agent_jobs,
            responses=self.responses,
            tool_context_factory=self._tool_context,
            action_queue=self.action_queue,
            events=self.events,
        )
        self._null_perf = NullPerfRecorder()
        self._request_context: ContextVar[_PipelineRequestContext | None] = ContextVar(
            f"assistant_pipeline_request_{id(self)}",
            default=None,
        )

    def handle_text(
        self,
        user: UserContext,
        text: str,
        *,
        idempotency_key: str = "",
        trace_id: str = "",
    ) -> AssistantReply:
        recorder = PerfRecorder()
        trace_id = trace_id or new_trace_id()
        request_context = _PipelineRequestContext(
            perf=recorder,
            trace_id=trace_id,
            idempotency_key=idempotency_key,
        )
        context_token = self._request_context.set(request_context)
        try:
            with recorder.track("total_response"):
                reply = self._handle_text(user, text)
            reply = replace(reply, perf_ms=recorder.snapshot_ms(), trace_id=reply.trace_id or trace_id)
            turn = self.conversation_turns.add(
                user_id=user.user_id,
                user_text=text,
                assistant_text=reply.text,
                extracted_actions=actions_to_dicts(request_context.extracted_actions),
            )
            return replace(reply, conversation_turn_id=getattr(turn, "id", None))
        finally:
            self._request_context.reset(context_token)

    def rewrite_shorter(
        self,
        user: UserContext,
        source_turn: ConversationTurn,
        *,
        trace_id: str = "",
    ) -> AssistantReply:
        """Create another candidate reply; it is stored only after explicit feedback."""
        if not self._consume_ai_limit(user):
            return self.responses.daily_limit(intent=Intent.ASK)
        recorder = PerfRecorder()
        trace_id = trace_id or new_trace_id()
        request_context = _PipelineRequestContext(perf=recorder, trace_id=trace_id)
        context_token = self._request_context.set(request_context)
        prompt = (
            "Перепиши ответ короче и яснее. Верни только готовый текст ответа, без пояснений о правке. "
            "Не добавляй новых фактов.\n\n"
            f"Запрос пользователя:\n{source_turn.user_text}\n\n"
            f"Исходный ответ:\n{source_turn.assistant_text}"
        )
        try:
            with recorder.track("total_response"):
                reply = answer_with_ai(
                    hermes=self.hermes,
                    responses=self.responses,
                    user=user,
                    prompt=prompt,
                    intent=Intent.ASK,
                    style=self.preferences.get(user.user_id).preferred_response_style,
                    communication_style=self.communication_style,
                    max_output_chars=min(self.max_output_chars, 700),
                    perf=self._perf,
                    trace_id=trace_id,
                    events=self.events,
                )
            reply = replace(reply, perf_ms=recorder.snapshot_ms(), trace_id=reply.trace_id or trace_id)
            turn = self.conversation_turns.add(
                user_id=user.user_id,
                user_text=source_turn.user_text,
                assistant_text=reply.text,
                extracted_actions=[],
            )
            return replace(reply, conversation_turn_id=getattr(turn, "id", None))
        finally:
            self._request_context.reset(context_token)

    @property
    def _perf(self):
        request_context = self._request_context.get()
        return request_context.perf if request_context is not None else self._null_perf

    @property
    def _current_trace_id(self) -> str:
        request_context = self._request_context.get()
        return request_context.trace_id if request_context is not None else ""

    @property
    def _current_idempotency_key(self) -> str:
        request_context = self._request_context.get()
        return request_context.idempotency_key if request_context is not None else ""

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
            if getattr(self.limits, "is_unlimited", lambda: False)():
                return AssistantReply(text="AI включён. Лимит запросов отключён.", intent=parsed.intent)
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
                    events=self.events,
                    worker_leases=self.worker_leases,
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
        if parsed.intent == Intent.NOTES:
            return self._notes(user, parsed.text)
        if parsed.intent == Intent.NOTE_CREATE:
            return self._note_create(user, parsed.text)
        if parsed.intent == Intent.NOTE_SEARCH:
            return self._note_search(user, parsed.text)
        if parsed.intent == Intent.NOTE_EDIT:
            return self._note_edit_last(user, parsed.text)
        if parsed.intent == Intent.NOTE_DELETE:
            return self._note_delete_last(user)
        if parsed.intent == Intent.CONTACT_ADD:
            return self._contact_add(user, parsed.text)
        if parsed.intent == Intent.CONTACTS:
            return self._contacts(user)
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
        if parsed.intent == Intent.MONITOR_ADD:
            return self._monitor_add(user, parsed.text)
        if parsed.intent == Intent.MONITOR_LIST:
            return self._monitor_list(user)
        if parsed.intent == Intent.MONITOR_REMOVE:
            return self._monitor_remove(user, parsed.text)
        if parsed.intent in {Intent.ASK, Intent.UNKNOWN} and not (parsed.raw_text or "").strip().startswith("/"):
            natural_route = natural_route or self._route_natural_text(user, parsed.raw_text)
            if natural_route.actions:
                return self._execute_natural_route(user, natural_route)

        input_gate = check_input(parsed.text, max_chars=self.max_input_chars)
        if not input_gate.ok:
            return self.responses.blocked_action(input_gate.reason, intent=parsed.intent)

        if parsed.intent == Intent.ASK and should_try_llm_action_extractor(input_gate.safe_text):
            if not self._consume_ai_limit(user):
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

        if not self._consume_ai_limit(user):
            return self.responses.daily_limit(intent=parsed.intent)

        return answer_with_ai(
            hermes=self.hermes,
            responses=self.responses,
            user=user,
            prompt=input_gate.safe_text,
            intent=parsed.intent,
            style=self.preferences.get(user.user_id).preferred_response_style,
            communication_style=self.communication_style,
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
            item = self.knowledge.create(
                user_id=user.user_id,
                text=input_gate.safe_text,
                note_type="memory",
                source="telegram",
            )
            self._mirror_legacy_memory(user.user_id, input_gate.safe_text)
        return AssistantReply(text=f"Сохранил важное #{item.id}.", intent=Intent.REMEMBER)

    def _memories(self, user: UserContext) -> AssistantReply:
        items = self.knowledge.list_for_user(user.user_id, note_type="memory")
        if not items:
            return AssistantReply(text="Пока нет сохранённых заметок.", intent=Intent.MEMORIES)
        lines = [f"{item.id}. {item.text}" for item in items]
        return AssistantReply(text="Сохранённое:\n" + "\n".join(lines), intent=Intent.MEMORIES)

    def _idea(self, user: UserContext, text: str) -> AssistantReply:
        input_gate = check_input(text, max_chars=1000)
        if not input_gate.ok:
            return self.responses.blocked_action(input_gate.reason, intent=Intent.IDEA)
        with self._perf.track("tool"):
            item = self.knowledge.create(
                user_id=user.user_id,
                text=input_gate.safe_text,
                note_type="idea",
                source="telegram",
            )
            self._mirror_legacy_idea(user.user_id, input_gate.safe_text)
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
        items = self.knowledge.list_for_user(user.user_id, note_type="idea")
        if not items:
            return AssistantReply(text="Пока нет идей.", intent=Intent.IDEAS)
        lines = [f"{item.id}. {item.text}" for item in items]
        return AssistantReply(text="Идеи:\n" + "\n".join(lines), intent=Intent.IDEAS)

    def _notes(self, user: UserContext, text: str) -> AssistantReply:
        value = (text or "").strip()
        if not value:
            return self._note_list(user)
        command, _, rest = value.partition(" ")
        normalized = command.lower()
        if normalized in {"search", "find", "найди", "поиск"}:
            return self._note_search(user, rest.strip())
        if normalized in {"edit", "update", "измени"}:
            return self._note_edit_command(user, rest.strip())
        if normalized in {"delete", "remove", "удали"}:
            return self._note_delete_command(user, rest.strip())
        return self._note_create(user, value)

    def _note_create(self, user: UserContext, text: str) -> AssistantReply:
        fields = fields_payload(text, fallback_key="text")
        note_text = fields.get("text", "")
        input_gate = check_input(note_text, max_chars=1500)
        if not input_gate.ok:
            return self.responses.blocked_action(input_gate.reason, intent=Intent.NOTE_CREATE)
        if not input_gate.safe_text:
            return AssistantReply(
                text="Напиши текст заметки: /notes текст",
                intent=Intent.NOTE_CREATE,
                blocked_reason="note_text_empty",
            )
        retention_days = _parse_optional_int(fields.get("retention_days") or fields.get("retention"))
        with self._perf.track("tool"):
            note = self.knowledge.create(
                user_id=user.user_id,
                text=input_gate.safe_text,
                note_type=fields.get("type") or "note",
                source=fields.get("source") or "telegram",
                project=fields.get("project"),
                contact=fields.get("contact"),
                retention_days=retention_days,
            )
        return AssistantReply(text=f"Сохранил заметку #{note.id}.", intent=Intent.NOTE_CREATE)

    def _note_list(self, user: UserContext) -> AssistantReply:
        items = self.knowledge.list_for_user(user.user_id)
        if not items:
            return AssistantReply(text="Заметок пока нет.", intent=Intent.NOTES)
        return AssistantReply(text="Заметки:\n" + "\n".join(_format_note_line(note) for note in items), intent=Intent.NOTES)

    def _note_search(self, user: UserContext, text: str) -> AssistantReply:
        query = (text or "").strip()
        if not query:
            return AssistantReply(
                text="Напиши, что искать: /notes search OAuth",
                intent=Intent.NOTE_SEARCH,
                blocked_reason="note_search_empty",
            )
        items = self.knowledge.search(user.user_id, query)
        if not items:
            return AssistantReply(text=f"Не нашёл заметки про: {query}", intent=Intent.NOTE_SEARCH)
        return AssistantReply(
            text="Нашёл заметки:\n" + "\n".join(_format_note_line(note) for note in items),
            intent=Intent.NOTE_SEARCH,
        )

    def _note_edit_command(self, user: UserContext, text: str) -> AssistantReply:
        value = (text or "").strip()
        lowered = value.lower()
        for prefix in ("last ", "последнюю ", "последнее ", "последнюю на ", "последнее на "):
            if lowered.startswith(prefix):
                return self._note_edit_last(user, value[len(prefix) :].strip())
        if lowered.startswith("last"):
            return self._note_edit_last(user, value[4:].strip())
        return self._note_edit_last(user, value)

    def _note_edit_last(self, user: UserContext, text: str) -> AssistantReply:
        input_gate = check_input(text, max_chars=1500)
        if not input_gate.ok:
            return self.responses.blocked_action(input_gate.reason, intent=Intent.NOTE_EDIT)
        if not input_gate.safe_text:
            return AssistantReply(
                text="Напиши новый текст заметки.",
                intent=Intent.NOTE_EDIT,
                blocked_reason="note_edit_empty",
            )
        latest = self.knowledge.latest_for_user(user.user_id)
        if latest is None:
            return AssistantReply(text="Нет заметки, которую можно изменить.", intent=Intent.NOTE_EDIT)
        updated = self.knowledge.update(user.user_id, latest.id, text=input_gate.safe_text)
        if updated is None:
            return AssistantReply(text="Не нашёл заметку для изменения.", intent=Intent.NOTE_EDIT)
        return AssistantReply(text=f"Обновил заметку #{updated.id}.", intent=Intent.NOTE_EDIT)

    def _note_delete_command(self, user: UserContext, text: str) -> AssistantReply:
        value = (text or "").strip().lower()
        if value in {"", "last", "последнюю", "последнее", "её", "ее"}:
            return self._note_delete_last(user)
        if value.isdigit():
            return self._note_delete_id(user, int(value))
        return AssistantReply(
            text="Укажи заметку: /notes delete last или /notes delete 3",
            intent=Intent.NOTE_DELETE,
            blocked_reason="note_delete_bad_id",
        )

    def _note_delete_last(self, user: UserContext) -> AssistantReply:
        latest = self.knowledge.latest_for_user(user.user_id)
        if latest is None:
            return AssistantReply(text="Нет заметки, которую можно удалить.", intent=Intent.NOTE_DELETE)
        return self._delete_note(user, latest.id)

    def _note_delete_id(self, user: UserContext, note_id: int) -> AssistantReply:
        return self._delete_note(user, note_id)

    def _delete_note(self, user: UserContext, note_id: int) -> AssistantReply:
        deleted = self.knowledge.delete(user.user_id, note_id)
        if not deleted:
            return AssistantReply(text=f"Не нашёл заметку #{note_id}.", intent=Intent.NOTE_DELETE)
        return AssistantReply(text=f"Удалил заметку #{note_id}.", intent=Intent.NOTE_DELETE)

    def _contact_add(self, user: UserContext, text: str) -> AssistantReply:
        fields = fields_payload(text, fallback_key="name")
        name = (fields.get("name") or "").strip()
        if name.lower().startswith("add "):
            name = name[4:].strip()
        if not name:
            return AssistantReply(
                text="Формат: /contact add Илья | alias=илье,илюха | tg_user_id=123 | chat_id=123",
                intent=Intent.CONTACT_ADD,
                blocked_reason="contact_name_empty",
            )
        aliases = _split_aliases(fields.get("aliases") or fields.get("alias") or "")
        tg_user_id = _parse_optional_int(fields.get("tg_user_id"))
        chat_id = _parse_optional_int(fields.get("chat_id"))
        if tg_user_id is None and chat_id is None:
            return AssistantReply(
                text="Укажи tg_user_id или chat_id контакта.",
                intent=Intent.CONTACT_ADD,
                blocked_reason="contact_telegram_id_missing",
            )
        try:
            contact = self.contact_book.upsert(
                user_id=user.user_id,
                name=name,
                aliases=aliases,
                tg_user_id=tg_user_id,
                chat_id=chat_id,
            )
        except ValueError as exc:
            return AssistantReply(text=str(exc), intent=Intent.CONTACT_ADD, blocked_reason="contact_invalid")
        return AssistantReply(text=f"Сохранил контакт #{contact.id}: {contact.name}.", intent=Intent.CONTACT_ADD)

    def _contacts(self, user: UserContext) -> AssistantReply:
        contacts = self.contact_book.list_for_user(user.user_id)
        if not contacts:
            return AssistantReply(text="Контактов пока нет.", intent=Intent.CONTACTS)
        lines = []
        for contact in contacts:
            aliases = f" ({', '.join(contact.aliases)})" if contact.aliases else ""
            target = contact.chat_id or contact.tg_user_id or "no telegram id"
            lines.append(f"{contact.id}. {contact.name}{aliases} — {target}")
        return AssistantReply(text="Контакты:\n" + "\n".join(lines), intent=Intent.CONTACTS)

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
            item = self.reminders.add(
                user.user_id,
                input_gate.safe_text,
                parsed.remind_at,
                recurrence=parsed.recurrence,
            )
            self.docs_sync.append(
                kind="reminder",
                user_id=user.user_id,
                text=f"{item.remind_at.isoformat()} — {item.text}",
                created_at=item.remind_at,
                record_id=str(item.id),
            )
        return AssistantReply(
            text=_format_reminder_reply(item.remind_at, item.text, recurrence=item.recurrence),
            intent=Intent.REMIND,
        )

    def _reminders(self, user: UserContext) -> AssistantReply:
        items = self.reminders.list_pending_for_user(user.user_id)
        if not items:
            return AssistantReply(text="Активных напоминаний нет.", intent=Intent.REMINDERS)
        lines = [
            f"{item.id}. {item.remind_at.isoformat()} — {item.text}{' · каждый день' if item.recurrence == 'daily' else ''}"
            for item in items
        ]
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
        job = self.agent_jobs.create(
            user.user_id,
            input_gate.safe_text,
            steps,
            trace_id=self._current_trace_id,
            idempotency_key=(
                f"{self._current_idempotency_key}:job"
                if self._current_idempotency_key
                else None
            ),
        )
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

    def _monitor_add(self, user: UserContext, text: str) -> AssistantReply:
        if self.monitor_jobs is None:
            return AssistantReply(
                text="Monitors не подключены.",
                intent=Intent.MONITOR_ADD,
                blocked_reason="monitor_store_disabled",
            )
        parsed = _parse_monitor_add_payload(text)
        if parsed is None:
            return AssistantReply(
                text=(
                    "Формат: /monitor add github_releases owner/repo | "
                    "condition=напиши мне только если вышел важный релиз"
                ),
                intent=Intent.MONITOR_ADD,
                blocked_reason="monitor_add_bad_format",
            )
        source_type, source_config, condition_text = parsed.source_type, parsed.source_config, parsed.condition_text
        input_gate = check_input(condition_text, max_chars=800)
        if not input_gate.ok:
            return self.responses.blocked_action(input_gate.reason, intent=Intent.MONITOR_ADD)
        monitor = self.monitor_jobs.create(
            user_id=user.user_id,
            chat_id=user.tg_user_id,
            source_type=source_type,
            source_config=source_config,
            condition_text=input_gate.safe_text,
        )
        return AssistantReply(
            text=(
                f"Добавил monitor #{monitor.id}: {_format_monitor_source(monitor.source_type, monitor.source_config)}\n"
                f"Условие: {monitor.condition_text}\n"
                "Проверить: /monitor list"
            ),
            intent=Intent.MONITOR_ADD,
        )

    def _monitor_list(self, user: UserContext) -> AssistantReply:
        if self.monitor_jobs is None:
            return AssistantReply(
                text="Monitors не подключены.",
                intent=Intent.MONITOR_LIST,
                blocked_reason="monitor_store_disabled",
            )
        monitors = self.monitor_jobs.list_for_user(user.user_id)
        if not monitors:
            return AssistantReply(
                text="Активных proactive monitors нет. Добавить: /monitor add github_releases openai/codex | condition=важный релиз",
                intent=Intent.MONITOR_LIST,
            )
        lines = ["Proactive monitors:"]
        for monitor in monitors:
            status = "enabled" if monitor.enabled else "disabled"
            lines.append(
                f"{monitor.id}. {status} — {_format_monitor_source(monitor.source_type, monitor.source_config)} — {monitor.condition_text}"
            )
        return AssistantReply(text="\n".join(lines), intent=Intent.MONITOR_LIST)

    def _monitor_remove(self, user: UserContext, text: str) -> AssistantReply:
        if self.monitor_jobs is None:
            return AssistantReply(
                text="Monitors не подключены.",
                intent=Intent.MONITOR_REMOVE,
                blocked_reason="monitor_store_disabled",
            )
        value = (text or "").strip()
        if not value.isdigit():
            return AssistantReply(
                text="Укажи номер monitor: /monitor remove 1",
                intent=Intent.MONITOR_REMOVE,
                blocked_reason="monitor_remove_bad_id",
            )
        monitor_id = int(value)
        disabled = self.monitor_jobs.disable_for_user(user.user_id, monitor_id)
        if not disabled:
            return AssistantReply(
                text=f"Не нашёл monitor #{monitor_id}.",
                intent=Intent.MONITOR_REMOVE,
                blocked_reason="monitor_not_found",
            )
        return AssistantReply(text=f"Выключил monitor #{monitor_id}.", intent=Intent.MONITOR_REMOVE)

    def _execute_natural_route(self, user: UserContext, route: NaturalRoute) -> AssistantReply:
        request_context = self._request_context.get()
        if request_context is not None:
            request_context.extracted_actions[:] = route.actions
        return self.natural_actions.execute_route(
            user,
            route,
            perf=self._perf,
            trace_id=self._current_trace_id,
            idempotency_key=self._current_idempotency_key,
        )

    def _queue_direct_action(
        self,
        user: UserContext,
        action_type: ActionType,
        payload: dict[str, str],
    ) -> AssistantReply:
        return self.natural_actions.queue_direct_action(
            user,
            action_type,
            payload,
            trace_id=self._current_trace_id,
            idempotency_key=self._current_idempotency_key,
        )

    def execute_queued_action(self, user: UserContext, action: AgentAction) -> str:
        return self.natural_actions.execute_queued_action(user, action, perf=self._perf, trace_id=action.trace_id)

    def execute_queued_action_result(self, user: UserContext, action: AgentAction) -> ToolExecutionResult:
        return self.natural_actions.execute_queued_action_result(
            user,
            action,
            perf=self._perf,
            trace_id=action.trace_id,
        )

    def _tool_context(self, user: UserContext, idempotency_key: str = "") -> ToolContext:
        return ToolContext(
            user=user,
            memories=self.memories,
            ideas=self.ideas,
            reminders=self.reminders,
            docs_sync=self.docs_sync,
            task_center=self.task_center,
            agent_jobs=self.agent_jobs,
            knowledge=self.knowledge,
            contact_book=self.contact_book,
            delivery_outbox=self.delivery_outbox,
            preferences=self.preferences.get(user.user_id),
            idempotency_key=idempotency_key,
        )

    def _consume_ai_limit(self, user: UserContext) -> bool:
        if user.is_admin:
            return True
        return self.limits.consume(user.user_id)

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

    def _mirror_legacy_memory(self, user_id: int, text: str) -> None:
        if self._explicit_memories is not None and not _store_uses_knowledge(self._explicit_memories, self.knowledge):
            self._explicit_memories.add(user_id, text)

    def _mirror_legacy_idea(self, user_id: int, text: str) -> None:
        if self._explicit_ideas is not None and not _store_uses_knowledge(self._explicit_ideas, self.knowledge):
            self._explicit_ideas.add(user_id, text)


@dataclass(frozen=True)
class _MonitorAddPayload:
    source_type: str
    source_config: dict[str, object]
    condition_text: str


def _parse_monitor_add_payload(text: str) -> _MonitorAddPayload | None:
    left, separator, right = (text or "").partition("|")
    if not separator:
        return None
    parts = left.strip().split()
    if not parts:
        return None
    source_type = parts[0]
    options = _parse_monitor_options(right)
    condition_text = str(options.pop("condition", "")).strip()
    if not condition_text:
        return None

    if source_type == "github_releases":
        if len(parts) != 2:
            return None
        repo_ref = parts[1]
        if repo_ref.count("/") != 1:
            return None
        owner, repo = (part.strip() for part in repo_ref.split("/", 1))
        if not _valid_github_segment(owner) or not _valid_github_segment(repo):
            return None
        return _MonitorAddPayload(source_type, {"owner": owner, "repo": repo, **options}, condition_text)

    if source_type in {"rss", "http_api"}:
        if len(parts) != 2 or not _valid_https_url(parts[1]):
            return None
        config: dict[str, object] = {"url": parts[1], **options}
        if source_type == "http_api":
            allowed = config.get("allowed_hosts") or config.get("allowed_host")
            if isinstance(allowed, str):
                config["allowed_hosts"] = [item.strip() for item in allowed.split(",") if item.strip()]
            if not config.get("allowed_hosts"):
                return None
        return _MonitorAddPayload(source_type, config, condition_text)

    if source_type == "telegram_trends":
        if len(parts) != 1:
            return None
        return _MonitorAddPayload(source_type, dict(options), condition_text)

    return None


def _parse_monitor_options(text: str) -> dict[str, str]:
    options: dict[str, str] = {}
    for chunk in (text or "").split("|"):
        key, separator, value = chunk.strip().partition("=")
        if separator != "=":
            continue
        options[key.strip().lower()] = value.strip()
    return options


def _format_monitor_source(source_type: str, config: dict) -> str:
    if source_type == "github_releases":
        return f"github_releases {config.get('owner', '?')}/{config.get('repo', '?')}"
    if source_type in {"rss", "http_api"}:
        return f"{source_type} {config.get('url', '?')}"
    return source_type


def _valid_https_url(value: str) -> bool:
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.hostname:
        return False
    return True


def _valid_github_segment(value: str) -> bool:
    if not value or value in {".", ".."}:
        return False
    return all(char.isalnum() or char in {"-", "_", "."} for char in value)


def _knowledge_from_legacy(memories, ideas):
    for store in (memories, ideas):
        knowledge = getattr(store, "knowledge", None)
        if knowledge is not None:
            return knowledge
    return None


def _store_uses_knowledge(store, knowledge) -> bool:
    store_knowledge = getattr(store, "knowledge", None)
    if store_knowledge is None:
        return False
    if store_knowledge is knowledge:
        return True
    return getattr(store_knowledge, "session_factory", None) is getattr(knowledge, "session_factory", None)


def _format_note_line(note: Note) -> str:
    meta = []
    if note.note_type != "note":
        meta.append(note.note_type)
    if note.project:
        meta.append(note.project)
    if note.contact:
        meta.append(note.contact)
    suffix = f" ({', '.join(meta)})" if meta else ""
    return f"{note.id}. {note.text}{suffix}"


def _parse_optional_int(value: str | None) -> int | None:
    clean = (value or "").strip()
    if not clean:
        return None
    if not clean.isdigit():
        return None
    parsed = int(clean)
    return parsed if parsed > 0 else None


def _split_aliases(value: str) -> list[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]
