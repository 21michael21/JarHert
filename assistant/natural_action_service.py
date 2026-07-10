from __future__ import annotations

from collections.abc import Callable

from assistant.action_executor import ActionExecutor
from assistant.action_queue import AgentAction, ActionStatus
from assistant.action_schema import ActionType, NaturalRoute, PlannedAction
from assistant.agent_jobs import AgentJobStore
from assistant.command_handlers import is_heavy_action, natural_action_label
from assistant.natural_router import route_natural_text
from assistant.perf import PerfRecorder
from assistant.response_composer import ResponseComposer
from assistant.task_command_center import TaskCommandError
from assistant.tool_registry import ToolContext, ToolExecutionError, ToolExecutionResult, ToolRisk
from assistant.types import AssistantReply, Intent, ReplyButton, UserContext


ToolContextFactory = Callable[[UserContext, str], ToolContext]


class NaturalActionService:
    def __init__(
        self,
        *,
        action_executor: ActionExecutor,
        agent_jobs: AgentJobStore,
        responses: ResponseComposer,
        tool_context_factory: ToolContextFactory,
        action_queue=None,
        events=None,
    ) -> None:
        self.action_executor = action_executor
        self.agent_jobs = agent_jobs
        self.responses = responses
        self.tool_context_factory = tool_context_factory
        self.action_queue = action_queue
        self.events = events

    def route_text(
        self,
        *,
        user: UserContext,
        text: str,
        conversation_turns,
        preferences,
        perf: PerfRecorder,
    ) -> NaturalRoute:
        with perf.track("route"):
            return route_natural_text(
                text,
                context_text=conversation_turns.latest_user_text(user.user_id),
                preferences=preferences.get(user.user_id),
            )

    def execute_route(
        self,
        user: UserContext,
        route: NaturalRoute,
        *,
        perf: PerfRecorder,
        trace_id: str = "",
        idempotency_key: str = "",
    ) -> AssistantReply:
        if any(action.needs_confirmation for action in route.actions):
            return self.responses.clarification_question("natural_action_needs_clarification")
        if self.action_queue is not None and any(is_heavy_action(action) for action in route.actions):
            return self.queue_route(
                user,
                route,
                trace_id=trace_id,
                idempotency_key=idempotency_key,
            )
        if self.action_queue is None and any(self._needs_approval(action) for action in route.actions):
            return self.responses.clarification_question("action_needs_confirmation")

        done: list[str] = []
        failed: list[str] = []
        for index, action in enumerate(route.actions, start=1):
            try:
                summary = self.execute_action(
                    user,
                    action,
                    perf=perf,
                    trace_id=trace_id,
                    idempotency_key=(f"{idempotency_key}:action:{index}" if idempotency_key else ""),
                )
            except (TaskCommandError, ToolExecutionError) as exc:
                failed.append(f"{index}. {natural_action_label(action)}: {exc}")
                continue
            done.append(summary)

        if failed and not done:
            return self.responses.partial_failure(
                done=[],
                failed=failed,
                intent=Intent.AGENT_DO,
            )
        if failed:
            return self.responses.partial_failure(done=done, failed=failed, intent=Intent.AGENT_DO)
        return self.responses.success_summary(done=done, intent=Intent.AGENT_DO)

    def execute_action(
        self,
        user: UserContext,
        action: PlannedAction,
        *,
        perf: PerfRecorder,
        trace_id: str = "",
        idempotency_key: str = "",
    ) -> str:
        if action.type == ActionType.CALENDAR_MOVE:
            raise TaskCommandError("Перенос встреч пока требует уточнения и отдельного calendar update tool.")
        with perf.track("tool"):
            return self.action_executor.execute(
                action,
                self.tool_context_factory(user, idempotency_key),
            ).message

    def execute_action_result(
        self,
        user: UserContext,
        action: PlannedAction,
        *,
        perf: PerfRecorder,
        trace_id: str = "",
        idempotency_key: str = "",
    ) -> ToolExecutionResult:
        if action.type == ActionType.CALENDAR_MOVE:
            raise TaskCommandError("Перенос встреч пока требует уточнения и отдельного calendar update tool.")
        with perf.track("tool"):
            return self.action_executor.execute(
                action,
                self.tool_context_factory(user, idempotency_key),
            )

    def queue_route(
        self,
        user: UserContext,
        route: NaturalRoute,
        *,
        trace_id: str = "",
        idempotency_key: str = "",
    ) -> AssistantReply:
        labels = [natural_action_label(action) for action in route.actions]
        goal = "; ".join(labels)
        job_key = f"{idempotency_key}:job" if idempotency_key else None
        job = self.agent_jobs.create(
            user.user_id,
            goal,
            labels,
            trace_id=trace_id,
            idempotency_key=job_key,
        )
        self._log(user.user_id, "job_created", {"job_id": job.id, "goal": goal}, trace_id)
        pending_actions = []
        previous_action_id: int | None = None
        job_needs_confirmation = any(self._needs_approval(action) for action in route.actions)
        for index, action in enumerate(route.actions, start=1):
            queued = self.action_queue.enqueue(
                user_id=user.user_id,
                action_type=action.type,
                payload=action.payload,
                job_id=job.id,
                trace_id=trace_id,
                depends_on_action_id=previous_action_id,
                idempotency_key=(
                    f"{idempotency_key}:action:{index}"
                    if idempotency_key
                    else f"{user.user_id}:job:{job.id}:action:{index}"
                ),
                status=ActionStatus.NEEDS_CONFIRMATION if job_needs_confirmation else ActionStatus.QUEUED,
            )
            pending_actions.append(queued)
            previous_action_id = queued.id
            self._log(
                user.user_id,
                "action_needs_confirmation" if job_needs_confirmation else "action_queued",
                {
                    "job_id": job.id,
                    "action_id": queued.id,
                    "type": action.type.value,
                    "depends_on_action_id": queued.depends_on_action_id,
                },
                trace_id,
            )

        if any(action.status == ActionStatus.NEEDS_CONFIRMATION for action in pending_actions):
            lines = [f"Нужно одно подтверждение для Job #{job.id}:"]
            lines.extend(f"{index}. {label}" for index, label in enumerate(labels, start=1))
            lines.append("Подтверди один раз: выполню весь список по порядку.")
            return AssistantReply(
                text="\n".join(lines),
                intent=Intent.AGENT_DO,
                trace_id=trace_id,
                buttons=_approval_buttons(job.id),
            )

        return AssistantReply(
            text=f"Принял, выполняю. Job #{job.id}.\nИтог пришлю отдельным сообщением.",
            intent=Intent.AGENT_DO,
            trace_id=trace_id,
            buttons=[[ReplyButton("Статус job", f"ai:status:{job.id}")]],
        )

    def queue_direct_action(
        self,
        user: UserContext,
        action_type: ActionType,
        payload: dict[str, str],
        *,
        trace_id: str = "",
        idempotency_key: str = "",
    ) -> AssistantReply:
        route = NaturalRoute(actions=[PlannedAction(action_type, payload=payload)])
        return self.queue_route(
            user,
            route,
            trace_id=trace_id,
            idempotency_key=idempotency_key,
        )

    def execute_queued_action(
        self,
        user: UserContext,
        action: AgentAction,
        *,
        perf: PerfRecorder,
        trace_id: str = "",
    ) -> str:
        return self.execute_queued_action_result(user, action, perf=perf, trace_id=trace_id).message

    def execute_queued_action_result(
        self,
        user: UserContext,
        action: AgentAction,
        *,
        perf: PerfRecorder,
        trace_id: str = "",
    ) -> ToolExecutionResult:
        planned = PlannedAction(action.type, payload=action.payload, reason="queued_action")
        return self.execute_action_result(
            user,
            planned,
            perf=perf,
            trace_id=trace_id or action.trace_id,
            idempotency_key=action.idempotency_key or "",
        )

    def _needs_approval(self, action: PlannedAction) -> bool:
        spec = self.action_executor.registry.get(action.type)
        return spec.risk in {ToolRisk.MEDIUM, ToolRisk.HIGH}

    def _log(self, user_id: int, event_type: str, meta: dict, trace_id: str) -> None:
        if self.events is not None:
            self.events.log(user_id, event_type, meta, trace_id=trace_id)


def _approval_buttons(job_id: int) -> list[list[ReplyButton]]:
    return [
        [
            ReplyButton("Подтвердить всё", f"ai:confirm_job:{job_id}"),
            ReplyButton("Отменить всё", f"ai:cancel_job:{job_id}"),
        ],
        [ReplyButton("Статус job", f"ai:status:{job_id}")],
    ]
