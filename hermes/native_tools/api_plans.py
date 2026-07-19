"""Action-plan methods of NativeToolsAPI, split out of the god object.

The mixin relies on the facade for stores and capabilities; the public
contract (method names and payloads) is unchanged.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from .action_plans import ActionPlan, execute_plan
from .delivery import HermesTelegramSender
from .personal_os import PersonalOSStore
from .personal_productivity import PersonalProductivityStore

if TYPE_CHECKING:
    from .mcp_api import Confirmer, NativeToolsAPI


logger = logging.getLogger(__name__)


class ActionPlansMixin:
    if TYPE_CHECKING:
        plan_receipt_sender: "NativeToolsAPI.plan_receipt_sender"
        _plans: "NativeToolsAPI._plans"
        _capabilities: "NativeToolsAPI._capabilities"
        _action_adapter: "NativeToolsAPI._action_adapter"

    def action_plan_create(
        self, *, actions: list[dict[str, Any]], idempotency_key: str
    ) -> dict[str, Any]:
        for action in actions:
            self._capabilities().require(str(action.get("type") or ""))
        plan = self._plans().create(actions, idempotency_key=idempotency_key)
        return _plan_payload(plan)

    def action_plan_approve(self, *, plan_id: int) -> dict[str, Any]:
        return _plan_payload(self._plans().approve(plan_id))

    def action_plan_execute(self, *, plan_id: int, confirmed: bool = False) -> dict[str, Any]:
        if not confirmed:
            raise ValueError("Plan execution требует подтверждение пользователя.")
        store = self._plans()
        for action in store.get(plan_id).actions:
            self._capabilities().require(action.action_type)
        if store.get(plan_id).status == "draft":
            store.approve(plan_id)
        return _plan_payload(execute_plan(store, plan_id, self._action_adapter()))

    def action_plan_cancel(self, *, plan_id: int) -> dict[str, Any]:
        return _plan_payload(self._plans().cancel(plan_id))

    def action_plan_get(self, *, plan_id: int) -> dict[str, Any]:
        return _plan_payload(self._plans().get(plan_id))

    def action_plan_trace(self, *, plan_id: int) -> dict[str, Any]:
        return self._plans().compact_trace(plan_id)

    def action_plan_pause(self, *, plan_id: int) -> dict[str, Any]:
        self._capabilities().require("planner.control")
        return _plan_payload(self._plans().pause(plan_id))

    def action_plan_resume(self, *, plan_id: int) -> dict[str, Any]:
        self._capabilities().require("planner.control")
        return _plan_payload(self._plans().resume(plan_id))

    async def action_plan_confirm_execute(
        self,
        *,
        actions: list[dict[str, Any]],
        idempotency_key: str,
        confirmer: Confirmer,
    ) -> dict[str, Any]:
        for action in actions:
            self._capabilities().require(str(action.get("type") or ""))
        store = self._plans()
        plan = store.create(actions, idempotency_key=idempotency_key)
        if plan.status in {"succeeded", "partial", "failed"}:
            return _plan_payload(plan)
        if plan.status == "draft":
            if not await confirmer(_plan_preview(plan)):
                return _plan_payload(store.cancel(plan.id))
            store.approve(plan.id)
        completed = await asyncio.to_thread(execute_plan, store, plan.id, self._action_adapter())
        await self._deliver_plan_receipt(completed)
        return _plan_payload(completed)

    async def _deliver_plan_receipt(self, plan: ActionPlan) -> None:
        """Send one durable success receipt when Telegram transport drops the tool turn."""
        enabled = os.getenv("HERMES_ACTION_PLAN_RECEIPT_DELIVERY", "true").strip().casefold()
        if enabled not in {"1", "true", "yes", "on"} or plan.status != "succeeded":
            return
        try:
            chat_id = int(os.getenv("HERMES_OWNER_TELEGRAM_CHAT_ID", "0") or 0)
        except ValueError:
            return
        if chat_id <= 0:
            return
        sender = self.plan_receipt_sender or HermesTelegramSender()
        summary = f"Готово: план #{plan.id} выполнен."
        try:
            await asyncio.to_thread(sender, chat_id, summary)
        except Exception as error:
            # The plan has already completed durably. A gateway final response
            # may still arrive, so a receipt delivery failure must not undo it.
            logger.warning("Could not deliver action-plan receipt %s: %s", plan.id, error)

    async def action_plan_dag_confirm_execute(
        self,
        *,
        nodes: list[dict[str, Any]],
        idempotency_key: str,
        confirmer: Confirmer,
    ) -> dict[str, Any]:
        for node in nodes:
            self._capabilities().require(str(node.get("type") or ""))
        store = self._plans()
        plan = store.create_dag(nodes, idempotency_key=idempotency_key)
        if plan.status in {"succeeded", "partial", "failed"}:
            return _plan_payload(plan)
        if plan.status == "draft":
            if not await confirmer(_plan_preview(plan)):
                return _plan_payload(store.cancel(plan.id))
            store.approve(plan.id)
        completed = await asyncio.to_thread(execute_plan, store, plan.id, self._action_adapter())
        return _plan_payload(completed)


def _plan_payload(plan: ActionPlan) -> dict[str, Any]:
    return {
        "id": plan.id,
        "status": plan.status,
        "idempotency_key": plan.idempotency_key,
        "actions": [asdict(action) for action in plan.actions],
    }


def _plan_preview(plan: ActionPlan) -> str:
    rows = []
    for action in plan.actions:
        title = str(action.payload.get("title") or action.payload.get("subject") or "без названия")
        rows.append(f"{action.position + 1}. {action.action_type}: {title}")
    return "\n".join(rows)


class NativeActionAdapter:
    def __init__(
        self,
        task_calendar_factory: Any,
        personal_os: PersonalOSStore,
        productivity: PersonalProductivityStore,
    ) -> None:
        self.task_calendar_factory = task_calendar_factory
        self._task_calendar: Any | None = None
        self.personal_os = personal_os
        self.productivity = productivity

    def __getattr__(self, name: str) -> Any:
        if self._task_calendar is None:
            self._task_calendar = self.task_calendar_factory()
        return getattr(self._task_calendar, name)

    def execute_batch(self, actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if self._task_calendar is None:
            self._task_calendar = self.task_calendar_factory()
        handler = getattr(self._task_calendar, "execute_batch", None)
        if callable(handler):
            return handler(actions)
        handlers = {
            "task.create": "create_task",
            "task.move": "move_task",
            "task.priority": "set_task_priority",
            "task.done": "complete_task",
            "task.delete": "delete_task",
            "calendar.create": "create_calendar_event",
            "calendar.move": "move_calendar_event",
            "calendar.delete": "delete_calendar_event",
        }
        results: list[dict[str, Any]] = []
        for action in actions:
            try:
                result = getattr(self._task_calendar, handlers[str(action["type"])])(**action["payload"])
            except Exception as error:
                results.append({"ok": False, "error": str(error) or type(error).__name__})
            else:
                results.append({"ok": True, "result": str(result)})
        return results

    def save_note(self, **payload: Any) -> str:
        note = self.personal_os.upsert_memory_block(block_type="note", **payload)
        return f"saved note\nnote_id={note.id}"

    def create_commitment(self, **payload: Any) -> str:
        commitment = self.personal_os.create_commitment(**payload)
        if commitment.due_at:
            self.productivity.create_reminder(
                text=f"Срок обещания: {commitment.subject} — {commitment.content}",
                remind_at=commitment.due_at,
                idempotency_key=f"commitment:{commitment.id}:due",
                source_type="commitment",
                source_id=commitment.id,
            )
        return f"created commitment\ncommitment_id={commitment.id}"

    def create_reminder(self, **payload: Any) -> str:
        reminder = self.productivity.create_reminder(**payload)
        return f"created reminder\nreminder_id={reminder.id}"
