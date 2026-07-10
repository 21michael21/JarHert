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


Action = Annotated[
    Union[
        TaskCreateAction,
        TaskMoveAction,
        TaskDoneAction,
        TaskDeleteAction,
        CalendarCreateAction,
        CalendarMoveAction,
        CalendarDeleteAction,
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
async def action_plan_confirm_execute(
    actions: list[Action], idempotency_key: str, ctx: Context
) -> dict[str, object]:
    """Create one exact Trello/Calendar plan, ask once, then execute it atomically."""
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
