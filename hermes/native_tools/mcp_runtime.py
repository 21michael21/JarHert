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
def monitor_list() -> dict[str, object]:
    """List configured proactive monitors."""
    return api.monitor_list()


@mcp.tool()
async def monitor_disable(monitor_id: int, ctx: Context) -> dict[str, object]:
    """Disable one proactive monitor while preserving its audit state."""
    if not await _confirm(ctx, f"Отключить monitor #{monitor_id}?"):
        return {"status": "unchanged", "monitor_id": monitor_id}
    return api.monitor_disable(monitor_id=monitor_id)


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
async def action_plan_confirm_execute(
    actions: list[Action], idempotency_key: str, ctx: Context
) -> dict[str, object]:
    """Create one notes/promises/Trello/Calendar plan, ask once, then execute it."""
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
