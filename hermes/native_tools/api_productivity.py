"""Reminders, CRM, trips, subscriptions and shopping methods of NativeToolsAPI."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from .api_payload import value_payload

if TYPE_CHECKING:
    from .mcp_api import NativeToolsAPI


logger = logging.getLogger(__name__)


class ProductivityMixin:
    if TYPE_CHECKING:
        _capabilities: "NativeToolsAPI._capabilities"
        _productivity: "NativeToolsAPI._productivity"
        _crm: "NativeToolsAPI._crm"
        _trips: "NativeToolsAPI._trips"
        _subscriptions: "NativeToolsAPI._subscriptions"
        _shopping: "NativeToolsAPI._shopping"
        _sync_subscriptions: "NativeToolsAPI._sync_subscriptions"

    def reminder_create(self, **payload: Any) -> dict[str, Any]:
        self._capabilities().require("reminder.create")
        return value_payload(self._productivity().create_reminder(**payload))

    def reminder_list(self, *, status: str = "active", limit: int = 100) -> dict[str, Any]:
        self._capabilities().require("reminder.list")
        items = self._productivity().list_reminders(status=status, limit=limit)
        return {"items": [value_payload(item) for item in items]}

    def reminder_reschedule(
        self,
        *,
        reminder_id: int,
        remind_at: str,
        recurrence: str | None = "keep",
    ) -> dict[str, Any]:
        self._capabilities().require("reminder.write")
        return value_payload(
            self._productivity().reschedule_reminder(
                reminder_id,
                remind_at=remind_at,
                recurrence=recurrence,
            )
        )

    def reminder_cancel(self, *, reminder_id: int) -> dict[str, Any]:
        self._capabilities().require("reminder.write")
        return value_payload(self._productivity().cancel_reminder(reminder_id))

    def crm_interaction_log(self, **payload: Any) -> dict[str, Any]:
        self._capabilities().require("crm.write")
        interaction = self._crm().log_interaction(**payload)
        if interaction.next_contact_at:
            self._productivity().create_reminder(
                text=f"Написать {interaction.contact}: {interaction.summary}",
                remind_at=interaction.next_contact_at,
                idempotency_key=f"crm-interaction:{interaction.id}:followup",
                source_type="crm_interaction",
                source_id=interaction.id,
            )
        return value_payload(interaction)

    def crm_timeline(
        self,
        *,
        contact: str | None = None,
        project: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        self._capabilities().require("crm.read")
        items = self._crm().list_interactions(
            contact=contact,
            project=project,
            limit=limit,
        )
        return {"items": [value_payload(item) for item in items]}

    def trip_create(
        self,
        *,
        name: str,
        destination: str,
        idempotency_key: str,
        starts_at: str | None = None,
        ends_at: str | None = None,
    ) -> dict[str, Any]:
        self._capabilities().require("trip.write")
        return value_payload(
            self._trips().create(
                name=name,
                destination=destination,
                starts_at=starts_at,
                ends_at=ends_at,
                idempotency_key=idempotency_key,
            )
        )

    def trip_list(self, *, status: str = "active", limit: int = 100) -> dict[str, Any]:
        self._capabilities().require("trip.read")
        return {"items": [value_payload(item) for item in self._trips().list(status=status, limit=limit)]}

    def trip_details(self, *, trip_id: int) -> dict[str, Any]:
        self._capabilities().require("trip.read")
        return {
            "trip": value_payload(self._trips().get(trip_id)),
            "items": [value_payload(item) for item in self._trips().list_items(trip_id)],
        }

    def trip_add_item(
        self,
        *,
        trip_id: int,
        kind: str,
        title: str,
        idempotency_key: str,
        details: str | None = None,
        due_at: str | None = None,
    ) -> dict[str, Any]:
        self._capabilities().require("trip.write")
        item = self._trips().add_item(
            trip_id=trip_id,
            kind=kind,
            title=title,
            details=details,
            due_at=due_at,
            idempotency_key=idempotency_key,
        )
        if item.due_at:
            self._productivity().sync_source_reminder(
                source_type="trip_item",
                source_id=item.id,
                text=f"Поездка: {item.title}",
                remind_at=item.due_at,
                idempotency_key=f"trip-item:{item.id}:due",
            )
        return value_payload(item)

    def trip_item_complete(self, *, item_id: int) -> dict[str, Any]:
        self._capabilities().require("trip.write")
        item = self._trips().complete_item(item_id)
        self._productivity().cancel_source_reminder(source_type="trip_item", source_id=item.id)
        return value_payload(item)

    def trip_cancel(self, *, trip_id: int) -> dict[str, Any]:
        self._capabilities().require("trip.cancel")
        item_ids = [item.id for item in self._trips().list_items(trip_id)]
        trip = self._trips().cancel(trip_id)
        for item_id in item_ids:
            self._productivity().cancel_source_reminder(source_type="trip_item", source_id=item_id)
        return value_payload(trip)

    def subscription_create(self, **payload: Any) -> dict[str, Any]:
        self._capabilities().require("subscription.write")
        item, created = self._subscriptions().create(**payload)
        self._productivity().sync_source_reminder(
            source_type="subscription",
            source_id=item.id,
            text=f"Списание {item.name}: {item.amount} {item.currency}",
            remind_at=item.next_charge_at,
            idempotency_key=f"subscription:{item.id}:charge",
        )
        if created:
            self._sync_subscriptions()
        return value_payload(item)

    def subscription_list(self, *, status: str = "active") -> dict[str, Any]:
        self._capabilities().require("subscription.read")
        return {
            "items": [value_payload(item) for item in self._subscriptions().list(status=status)],
            "monthly_totals": self._subscriptions().monthly_totals() if status == "active" else {},
        }

    def subscription_update(self, *, subscription_id: int, **payload: Any) -> dict[str, Any]:
        self._capabilities().require("subscription.write")
        item = self._subscriptions().update(subscription_id, **payload)
        self._productivity().sync_source_reminder(
            source_type="subscription",
            source_id=item.id,
            text=f"Списание {item.name}: {item.amount} {item.currency}",
            remind_at=item.next_charge_at,
            idempotency_key=f"subscription:{item.id}:charge",
        )
        self._sync_subscriptions()
        return value_payload(item)

    def subscription_cancel(self, *, subscription_id: int) -> dict[str, Any]:
        self._capabilities().require("subscription.write")
        item = self._subscriptions().cancel(subscription_id)
        self._productivity().cancel_source_reminder(source_type="subscription", source_id=item.id)
        self._sync_subscriptions()
        return value_payload(item)

    def shopping_add(
        self,
        *,
        text: str,
        idempotency_key: str,
        category: str | None = None,
        quantity: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        self._capabilities().require("shopping.write")
        return value_payload(
            self._shopping().add(
                text=text,
                category=category,
                quantity=quantity,
                project=project,
                idempotency_key=idempotency_key,
            )
        )

    def shopping_list(
        self,
        *,
        status: str = "needed",
        project: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        self._capabilities().require("shopping.read")
        return {"items": [value_payload(item) for item in self._shopping().list(status=status, project=project, limit=limit)]}

    def shopping_mark_bought(self, *, item_id: int) -> dict[str, Any]:
        self._capabilities().require("shopping.write")
        return value_payload(self._shopping().mark_bought(item_id))

    def shopping_remove(self, *, item_id: int) -> dict[str, Any]:
        self._capabilities().require("shopping.write")
        return value_payload(self._shopping().remove(item_id))
