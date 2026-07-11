from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated, Literal, Union

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from native_tools.mcp_api import NativeToolsAPI
else:
    from .mcp_api import NativeToolsAPI

from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel, Field


class Consent(BaseModel):
    pass


class ScheduledMessagePayload(BaseModel):
    contact: str
    text: str
    send_at: str


class NoteSavePayload(BaseModel):
    subject: str
    content: str
    project: str | None = None


class CommitmentCreatePayload(BaseModel):
    subject: str
    content: str
    contact: str | None = None
    project: str | None = None
    due_at: str | None = None


class ReminderCreatePayload(BaseModel):
    text: str
    remind_at: str
    recurrence: Literal["daily", "weekly", "monthly"] | None = None


class SkillStep(BaseModel):
    tool: str
    summary: str


MemoryBlockType = Literal["profile", "person", "project", "commitment", "preference"]
ProjectTool = Literal["tasks", "calendar", "notes", "reminders", "contacts", "messages", "monitors", "sandbox"]


class TaskCreatePayload(BaseModel):
    title: str
    list_name: str = "Inbox"
    project: str | None = None
    priority: str | None = None
    due: str | None = None
    description: str | None = None


class TaskMovePayload(BaseModel):
    title: str
    target_list: str


class TaskDonePayload(BaseModel):
    title: str
    summary: str = "Готово."


class TaskDeletePayload(BaseModel):
    title: str


class CalendarCreatePayload(BaseModel):
    title: str
    start: str
    end: str
    reminder_minutes: int | None = None
    description: str | None = None


class CalendarMovePayload(BaseModel):
    title: str
    start: str
    end: str


class CalendarDeletePayload(BaseModel):
    title: str


class TaskCreateAction(BaseModel):
    type: Literal["task.create"]
    payload: TaskCreatePayload


class TaskMoveAction(BaseModel):
    type: Literal["task.move"]
    payload: TaskMovePayload


class TaskDoneAction(BaseModel):
    type: Literal["task.done"]
    payload: TaskDonePayload


class TaskDeleteAction(BaseModel):
    type: Literal["task.delete"]
    payload: TaskDeletePayload


class CalendarCreateAction(BaseModel):
    type: Literal["calendar.create"]
    payload: CalendarCreatePayload


class CalendarMoveAction(BaseModel):
    type: Literal["calendar.move"]
    payload: CalendarMovePayload


class CalendarDeleteAction(BaseModel):
    type: Literal["calendar.delete"]
    payload: CalendarDeletePayload


class NoteSaveAction(BaseModel):
    type: Literal["note.save"]
    payload: NoteSavePayload


class CommitmentCreateAction(BaseModel):
    type: Literal["commitment.create"]
    payload: CommitmentCreatePayload


class ReminderCreateAction(BaseModel):
    type: Literal["reminder.create"]
    payload: ReminderCreatePayload


Action = Annotated[
    Union[
        TaskCreateAction,
        TaskMoveAction,
        TaskDoneAction,
        TaskDeleteAction,
        CalendarCreateAction,
        CalendarMoveAction,
        CalendarDeleteAction,
        NoteSaveAction,
        CommitmentCreateAction,
        ReminderCreateAction,
    ],
    Field(discriminator="type"),
]


api = NativeToolsAPI()
mcp = FastMCP("jarhert-native")


async def _confirm(ctx: Context, message: str) -> bool:
    result = await ctx.elicit(message=message, schema=Consent)
    return result.action == "accept"


@mcp.tool()
def integration_health() -> dict[str, bool]:
    """Check whether Trello and Google Calendar adapters are ready."""
    return api.integration_health()


@mcp.tool()
def task_list(list_name: str | None = None) -> dict[str, str]:
    """List Trello tasks, optionally from one list."""
    return api.task_list(list_name=list_name)


@mcp.tool()
def calendar_list(when: str = "today") -> dict[str, str]:
    """List Google Calendar events for today or tomorrow."""
    return api.calendar_list(when=when)


@mcp.tool()
def contact_add(name: str, telegram_chat_id: int, aliases: list[str] | None = None) -> dict[str, object]:
    """Save one exact Telegram contact and optional aliases."""
    return api.contact_add(name=name, telegram_chat_id=telegram_chat_id, aliases=aliases or [])


@mcp.tool()
def contact_list() -> dict[str, object]:
    """List saved Telegram contacts without guessing recipients."""
    return api.contact_list()


@mcp.tool()
async def message_plan_confirm_schedule(
    items: Annotated[list[ScheduledMessagePayload], Field(min_length=1, max_length=20)],
    idempotency_key: str,
    ctx: Context,
) -> dict[str, object]:
    """Preview a complete Telegram message plan once, then schedule every item."""
    return await api.message_plan_confirm_schedule(
        items=[item.model_dump() for item in items],
        idempotency_key=idempotency_key,
        confirmer=lambda preview: _confirm(ctx, f"Запланировать сообщения?\n{preview}"),
    )


@mcp.tool()
async def message_plan_cancel_confirmed(plan_id: int, ctx: Context) -> dict[str, object]:
    """Ask once, then cancel a draft or scheduled Telegram message plan."""
    if not await _confirm(ctx, f"Отменить план сообщений #{plan_id}?"):
        return {"status": "unchanged", "plan_id": plan_id}
    return api.message_plan_cancel(plan_id=plan_id)


@mcp.tool()
async def monitor_add_github_releases(
    name: str, owner: str, repo: str, condition: str, ctx: Context
) -> dict[str, object]:
    """Add one diff-first GitHub latest-release monitor."""
    if not await _confirm(ctx, f"Добавить monitor {owner}/{repo}: {condition}?"):
        return {"status": "unchanged"}
    return api.monitor_add_github_releases(name=name, owner=owner, repo=repo, condition=condition)


@mcp.tool()
async def monitor_add_source(
    name: str,
    source_type: Literal["rss", "json_api", "allowed_url"],
    url: str,
    allowed_hosts: Annotated[list[str], Field(min_length=1, max_length=20)],
    condition: str,
    ctx: Context,
    quiet_hours: str | None = None,
    timezone_name: str = "Europe/Moscow",
) -> dict[str, object]:
    """Add one HTTPS source whose host is explicitly allowlisted."""
    if not await _confirm(ctx, f"Добавить monitor {name}: {url}?"):
        return {"status": "unchanged"}
    return api.monitor_add_source(
        name=name,
        source_type=source_type,
        url=url,
        allowed_hosts=allowed_hosts,
        condition=condition,
        quiet_hours=quiet_hours,
        timezone_name=timezone_name,
    )


@mcp.tool()
def monitor_list() -> dict[str, object]:
    """List configured proactive monitors."""
    return api.monitor_list()


@mcp.tool()
def monitor_digest() -> dict[str, object]:
    """Read deferred quiet-hours and over-budget monitor changes."""
    return api.monitor_digest()


@mcp.tool()
def monitor_digest_mark_delivered(item_ids: list[int]) -> dict[str, int]:
    """Acknowledge digest items only after composing their Telegram summary."""
    return api.monitor_digest_mark_delivered(item_ids=item_ids)


@mcp.tool()
async def monitor_disable(monitor_id: int, ctx: Context) -> dict[str, object]:
    """Disable one proactive monitor while preserving its audit state."""
    if not await _confirm(ctx, f"Отключить monitor #{monitor_id}?"):
        return {"status": "unchanged", "monitor_id": monitor_id}
    return api.monitor_disable(monitor_id=monitor_id)


@mcp.tool()
def skill_feedback(
    workflow_key: str,
    title: str,
    steps: Annotated[list[SkillStep], Field(min_length=2, max_length=12)],
    idempotency_key: str,
    useful: bool,
) -> dict[str, object]:
    """Record explicit useful/not-useful feedback for one successful workflow."""
    return api.skill_feedback(
        workflow_key=workflow_key,
        title=title,
        steps=[step.model_dump() for step in steps],
        idempotency_key=idempotency_key,
        useful=useful,
    )


@mcp.tool()
def skill_candidates(ready_only: bool = True) -> dict[str, object]:
    """List inert skill drafts; writing still requires a separate diff approval."""
    return api.skill_candidates(ready_only=ready_only)


@mcp.tool()
def memory_block_upsert(
    block_type: MemoryBlockType,
    subject: str,
    content: str,
    project: str | None = None,
) -> dict[str, object]:
    """Save an explicitly requested profile, person, project, commitment, or preference fact."""
    return api.memory_block_upsert(
        block_type=block_type,
        subject=subject,
        content=content,
        project=project,
    )


@mcp.tool()
def memory_block_list(
    block_type: MemoryBlockType | None = None,
    project: str | None = None,
    limit: Annotated[int, Field(ge=1, le=200)] = 50,
) -> dict[str, object]:
    """List structured personal memory without returning unrelated block types."""
    return api.memory_block_list(block_type=block_type, project=project, limit=limit)


@mcp.tool()
def memory_consolidation_list() -> dict[str, object]:
    """Read compact snapshots built only from explicitly confirmed facts."""
    return api.memory_consolidation_list()


@mcp.tool()
async def project_context_upsert(
    key: str,
    name: str,
    ctx: Context,
    aliases: list[str] | None = None,
    trello_board: str | None = None,
    trello_list: str | None = None,
    calendar_id: str | None = None,
    contacts: list[str] | None = None,
    tools: list[ProjectTool] | None = None,
    context_note: str | None = None,
) -> dict[str, object]:
    """Create or update one project context after an explicit user request."""
    if not await _confirm(ctx, f"Сохранить настройки проекта {name}?"):
        return {"status": "unchanged", "key": key}
    return api.project_context_upsert(
        key=key,
        name=name,
        aliases=aliases or [],
        trello_board=trello_board,
        trello_list=trello_list,
        calendar_id=calendar_id,
        contacts=contacts or [],
        tools=tools or [],
        context_note=context_note,
    )


@mcp.tool()
def project_context_list() -> dict[str, object]:
    """List active project contexts and their scoped integrations."""
    return api.project_context_list()


@mcp.tool()
def project_context_resolve(text: str) -> dict[str, object] | None:
    """Resolve one project from exact configured aliases in the user's text."""
    return api.project_context_resolve(text=text)


@mcp.tool()
def commitment_list(
    contact: str | None = None,
    project: str | None = None,
    status: Literal["open", "done", "cancelled"] = "open",
    limit: Annotated[int, Field(ge=1, le=200)] = 100,
) -> dict[str, object]:
    """List promises filtered by contact, project, and status."""
    return api.commitment_list(contact=contact, project=project, status=status, limit=limit)


@mcp.tool()
def reminder_create(
    text: str,
    remind_at: str,
    idempotency_key: str,
    recurrence: Literal["daily", "weekly", "monthly"] | None = None,
) -> dict[str, object]:
    """Create one idempotent reminder from an ISO timestamp with timezone."""
    return api.reminder_create(
        text=text,
        remind_at=remind_at,
        recurrence=recurrence,
        idempotency_key=idempotency_key,
    )


@mcp.tool()
def reminder_list(
    status: Literal["active", "sent", "cancelled"] = "active",
    limit: Annotated[int, Field(ge=1, le=200)] = 100,
) -> dict[str, object]:
    """List reminders so natural requests can refer to a real reminder id."""
    return api.reminder_list(status=status, limit=limit)


@mcp.tool()
def reminder_reschedule(
    reminder_id: int,
    remind_at: str,
    recurrence: Literal["keep", "none", "daily", "weekly", "monthly"] = "keep",
) -> dict[str, object]:
    """Move an existing reminder and optionally change or clear its recurrence."""
    return api.reminder_reschedule(
        reminder_id=reminder_id,
        remind_at=remind_at,
        recurrence=recurrence,
    )


@mcp.tool()
def reminder_cancel(reminder_id: int) -> dict[str, object]:
    """Cancel one active reminder by its owner-visible id."""
    return api.reminder_cancel(reminder_id=reminder_id)


@mcp.tool()
def crm_interaction_log(
    contact: str,
    kind: Literal["message", "call", "meeting", "agreement", "note"],
    summary: str,
    idempotency_key: str,
    project: str | None = None,
    occurred_at: str | None = None,
    next_contact_at: str | None = None,
) -> dict[str, object]:
    """Record a confirmed contact interaction and optional next follow-up date."""
    return api.crm_interaction_log(
        contact=contact,
        kind=kind,
        summary=summary,
        project=project,
        occurred_at=occurred_at,
        next_contact_at=next_contact_at,
        idempotency_key=idempotency_key,
    )


@mcp.tool()
def crm_timeline(
    contact: str | None = None,
    project: str | None = None,
    limit: Annotated[int, Field(ge=1, le=200)] = 100,
) -> dict[str, object]:
    """Read the factual contact timeline without exposing unrelated people."""
    return api.crm_timeline(contact=contact, project=project, limit=limit)


@mcp.tool()
def personal_today(
    now: str | None = None,
    timezone_name: str = "Europe/Moscow",
) -> dict[str, object]:
    """Collect today's tasks, calendar, reminders, promises, follow-ups, and top three."""
    return api.personal_today(now=now, timezone_name=timezone_name)


@mcp.tool()
def personal_daily_brief(
    now: str | None = None,
    timezone_name: str = "Europe/Moscow",
) -> dict[str, object]:
    """Build one factual morning brief without an extra LLM call."""
    return api.personal_daily_brief(now=now, timezone_name=timezone_name)


@mcp.tool()
def personal_weekly_review(
    now: str | None = None,
    timezone_name: str = "Europe/Moscow",
) -> dict[str, object]:
    """Summarize completed, moved, failed work and the next three commitments."""
    return api.personal_weekly_review(now=now, timezone_name=timezone_name)


@mcp.tool()
def subscription_create(
    name: str,
    amount: str,
    currency: str,
    cadence: Literal["weekly", "monthly", "yearly"],
    next_charge_at: str,
    idempotency_key: str,
    category: str | None = None,
) -> dict[str, object]:
    """Save one recurring payment and schedule its next charge reminder."""
    return api.subscription_create(
        name=name,
        amount=amount,
        currency=currency,
        cadence=cadence,
        next_charge_at=next_charge_at,
        category=category,
        idempotency_key=idempotency_key,
    )


@mcp.tool()
def subscription_list(status: Literal["active", "cancelled"] = "active") -> dict[str, object]:
    """List subscriptions and monthly totals grouped by currency."""
    return api.subscription_list(status=status)


@mcp.tool()
def subscription_update(
    subscription_id: int,
    amount: str | None = None,
    cadence: Literal["weekly", "monthly", "yearly"] | None = None,
    next_charge_at: str | None = None,
    category: str | None = None,
) -> dict[str, object]:
    """Update an existing subscription and move its charge reminder."""
    return api.subscription_update(
        subscription_id=subscription_id,
        amount=amount,
        cadence=cadence,
        next_charge_at=next_charge_at,
        category=category,
    )


@mcp.tool()
def subscription_cancel(subscription_id: int) -> dict[str, object]:
    """Cancel a subscription and its pending charge reminder."""
    return api.subscription_cancel(subscription_id=subscription_id)


@mcp.tool()
async def commitment_complete_confirmed(commitment_id: int, ctx: Context) -> dict[str, object]:
    """Ask once, then mark one open promise as done."""
    if not await _confirm(ctx, f"Отметить обещание #{commitment_id} выполненным?"):
        return {"status": "unchanged", "commitment_id": commitment_id}
    return api.commitment_complete(commitment_id=commitment_id)


@mcp.tool()
def work_mode_get() -> dict[str, object]:
    """Return the active fast, think, or code policy mode and its deadline."""
    return api.work_mode_get()


@mcp.tool()
def work_mode_set(mode: Literal["fast", "think", "code"]) -> dict[str, object]:
    """Change capability mode after an explicit user request."""
    return api.work_mode_set(mode=mode)


@mcp.tool()
async def coding_job_enqueue_confirmed(
    mode: Literal["coding", "research"],
    prompt: str,
    idempotency_key: str,
    ctx: Context,
    repository_url: str | None = None,
    source_urls: list[str] | None = None,
) -> dict[str, object]:
    """Preview once, then queue work for an isolated remote runner."""
    if not await _confirm(ctx, f"Поставить {mode} job в изолированную очередь?\n{prompt[:300]}"):
        return {"status": "unchanged"}
    return api.coding_job_enqueue(
        mode=mode,
        prompt=prompt,
        repository_url=repository_url,
        source_urls=source_urls or [],
        idempotency_key=idempotency_key,
    )


@mcp.tool()
async def action_plan_confirm_execute(
    actions: list[Action], idempotency_key: str, ctx: Context
) -> dict[str, object]:
    """Create one notes/promises/reminders/Trello/Calendar plan and confirm it once."""
    payload = [action.model_dump(exclude_none=True) for action in actions]
    return await api.action_plan_confirm_execute(
        actions=payload,
        idempotency_key=idempotency_key,
        confirmer=lambda preview: _confirm(ctx, f"Выполнить этот план?\n{preview}"),
    )


@mcp.tool()
async def telegram_text_export_confirmed(
    peer: str,
    ctx: Context,
    output_format: Literal["txt", "jsonl"] = "txt",
    limit: Annotated[int, Field(ge=1, le=50000)] = 5000,
) -> dict[str, object]:
    """Ask once, then export text-only history from an owner-accessible Telegram dialog."""
    return await api.telegram_text_export_confirmed(
        peer=peer,
        output_format=output_format,
        limit=limit,
        confirmer=lambda preview: _confirm(ctx, preview),
    )


if __name__ == "__main__":
    mcp.run(transport="stdio")
