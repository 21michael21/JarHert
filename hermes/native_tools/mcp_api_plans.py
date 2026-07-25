from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import asdict
from typing import Any

from .action_plans import ActionPlan, execute_plan
from .delivery import HermesTelegramSender
from .mcp_api import Confirmer

logger = logging.getLogger(__name__)


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


class PlansMixin:
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
        trace = self._plans().compact_trace(plan_id)
        action_ids = {
            int(action["id"])
            for action in self.action_plan_get(plan_id=plan_id)["actions"]
            if isinstance(action, dict) and str(action.get("id") or "").isdigit()
        }
        recent_events = []
        for event in reversed(self._events().list_events()):
            payload = event.payload
            if payload.get("plan_id") != plan_id and payload.get("action_id") not in action_ids:
                continue
            recent_events.append(
                {
                    "event_type": event.event_type,
                    "source": event.source,
                    "status": event.status,
                }
            )
            if len(recent_events) >= 8:
                break
        return {**trace, "recent_events": recent_events}

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
        decisions = [
            self._capabilities().require(str(action.get("type") or ""))
            for action in actions
        ]
        store = self._plans()
        plan = store.create(actions, idempotency_key=idempotency_key)
        if plan.status in {"succeeded", "partial", "failed"}:
            return _plan_payload(plan)
        if plan.status == "draft":
            needs_confirmation = any(decision.decision != "auto" for decision in decisions)
            if needs_confirmation and not await confirmer(_plan_preview(plan)):
                return _plan_payload(store.cancel(plan.id))
            store.approve(plan.id)
        completed = execute_plan(store, plan.id, self._action_adapter())
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
        decisions = [
            self._capabilities().require(str(node.get("type") or ""))
            for node in nodes
        ]
        store = self._plans()
        plan = store.create_dag(nodes, idempotency_key=idempotency_key)
        if plan.status in {"succeeded", "partial", "failed"}:
            return _plan_payload(plan)
        if plan.status == "draft":
            needs_confirmation = any(decision.decision != "auto" for decision in decisions)
            if needs_confirmation and not await confirmer(_plan_preview(plan)):
                return _plan_payload(store.cancel(plan.id))
            store.approve(plan.id)
        return _plan_payload(execute_plan(store, plan.id, self._action_adapter()))
