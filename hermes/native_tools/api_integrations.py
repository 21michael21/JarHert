"""Monitors, knowledge archive, skills, contacts and voice methods of NativeToolsAPI."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .api_payload import value_payload

if TYPE_CHECKING:
    from .mcp_api import Confirmer, NativeToolsAPI


class IntegrationsMixin:
    if TYPE_CHECKING:
        _capabilities: "NativeToolsAPI._capabilities"
        _contacts: "NativeToolsAPI._contacts"
        _monitors: "NativeToolsAPI._monitors"
        _knowledge: "NativeToolsAPI._knowledge"
        _github_public: "NativeToolsAPI._github_public"
        _skills: "NativeToolsAPI._skills"
        _voice_vocabulary: "NativeToolsAPI._voice_vocabulary"

    def contact_add(self, *, name: str, telegram_chat_id: int, aliases: list[str]) -> dict[str, Any]:
        self._capabilities().require("contact.write")
        return value_payload(
            self._contacts().add_contact(
                name=name,
                telegram_chat_id=telegram_chat_id,
                aliases=aliases,
            )
        )

    def contact_list(self) -> dict[str, Any]:
        self._capabilities().require("contact.list")
        return {"items": [value_payload(item) for item in self._contacts().list_contacts()]}

    async def message_plan_confirm_schedule(
        self,
        *,
        items: list[dict[str, Any]],
        idempotency_key: str,
        confirmer: Confirmer,
    ) -> dict[str, Any]:
        self._capabilities().require("message.schedule")
        store = self._contacts()
        plan = store.create_message_plan(items, idempotency_key=idempotency_key)
        if plan.status != "draft":
            return value_payload(plan)
        if not await confirmer(_message_plan_preview(plan)):
            return value_payload(store.cancel_message_plan(plan.id))
        return value_payload(store.approve_message_plan(plan.id))

    def message_plan_cancel(self, *, plan_id: int) -> dict[str, Any]:
        self._capabilities().require("message.cancel")
        return value_payload(self._contacts().cancel_message_plan(plan_id))

    def monitor_add_github_releases(
        self,
        *,
        name: str,
        owner: str,
        repo: str,
        condition: str,
    ) -> dict[str, Any]:
        self._capabilities().require("monitor.write")
        return value_payload(
            self._monitors().add(
                name=name,
                source_type="github_releases",
                source_config={"owner": owner, "repo": repo},
                condition=condition,
            )
        )

    def monitor_add_source(
        self,
        *,
        name: str,
        source_type: str,
        url: str,
        allowed_hosts: list[str],
        condition: str,
        quiet_hours: str | None = None,
        timezone_name: str = "Europe/Moscow",
    ) -> dict[str, Any]:
        self._capabilities().require("monitor.write")
        source_config: dict[str, Any] = {
            "url": url,
            "allowed_hosts": allowed_hosts,
            "timezone": timezone_name,
        }
        if quiet_hours:
            source_config["quiet_hours"] = quiet_hours
        return value_payload(
            self._monitors().add(
                name=name,
                source_type=source_type,
                source_config=source_config,
                condition=condition,
            )
        )

    def monitor_list(self) -> dict[str, Any]:
        self._capabilities().require("monitor.list")
        return {"items": [value_payload(item) for item in self._monitors().list()]}

    def monitor_disable(self, *, monitor_id: int) -> dict[str, Any]:
        self._capabilities().require("monitor.write")
        return value_payload(self._monitors().disable(monitor_id))

    def monitor_schedule_update(
        self,
        *,
        monitor_id: int,
        quiet_hours: str | None,
        timezone_name: str = "Europe/Moscow",
    ) -> dict[str, Any]:
        """Keep changed monitor events quiet and collect them for the existing digest."""
        self._capabilities().require("monitor.write")
        return value_payload(
            self._monitors().update_schedule(
                monitor_id,
                quiet_hours=quiet_hours,
                timezone_name=timezone_name,
            )
        )

    def monitor_digest(self) -> dict[str, Any]:
        self._capabilities().require("monitor.list")
        return self._monitors().build_digest()

    def monitor_digest_mark_delivered(self, *, item_ids: list[int]) -> dict[str, int]:
        self._capabilities().require("monitor.write")
        self._monitors().mark_digest_delivered(item_ids)
        return {"delivered": len(set(int(item_id) for item_id in item_ids))}

    def knowledge_archive_url(self, *, url: str, project: str | None = None) -> dict[str, Any]:
        self._capabilities().require("knowledge.write")
        return self._knowledge().archive_url(url, project=project)

    def knowledge_archive_urls(self, *, urls: list[str], project: str | None = None) -> dict[str, Any]:
        self._capabilities().require("knowledge.write")
        if not urls or len(urls) > 20:
            raise ValueError("Для архива укажи от 1 до 20 явных URL.")
        items: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        archive = self._knowledge()
        for url in urls:
            try:
                items.append(archive.archive_url(str(url), project=project))
            except (OSError, ValueError) as error:
                errors.append({"url": str(url), "error": type(error).__name__})
        return {"items": items, "errors": errors}

    def knowledge_search(
        self,
        *,
        query: str,
        project: str | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        self._capabilities().require("knowledge.read")
        return {"items": self._knowledge().search(query, project=project, limit=limit)}

    def knowledge_source_excerpt(
        self,
        *,
        source_id: int,
        query: str | None = None,
    ) -> dict[str, Any]:
        self._capabilities().require("knowledge.read")
        return self._knowledge().source_excerpt(source_id, query=query)

    def knowledge_list_sources(
        self,
        *,
        project: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        self._capabilities().require("knowledge.read")
        return {"items": [value_payload(item) for item in self._knowledge().list_sources(project=project, limit=limit)]}

    def github_public_repository(self, *, url: str) -> dict[str, Any]:
        self._capabilities().require("github.read")
        return self._github_public().inspect_repository(url)

    def skill_feedback(
        self,
        *,
        workflow_key: str,
        title: str,
        steps: list[dict[str, Any]],
        idempotency_key: str,
        useful: bool,
    ) -> dict[str, Any]:
        self._capabilities().require("skill.feedback")
        return value_payload(
            self._skills().observe(
                workflow_key=workflow_key,
                title=title,
                steps=steps,
                idempotency_key=idempotency_key,
                success=True,
                confirmed=bool(useful),
            )
        )

    def skill_candidates(self, *, ready_only: bool = False) -> dict[str, Any]:
        self._capabilities().require("skill.list")
        items = self._skills().list_candidates(ready_only=ready_only)
        return {"items": [value_payload(item) for item in items]}

    def skill_mark_staged(self, *, workflow_key: str) -> dict[str, Any]:
        self._capabilities().require("skill.feedback")
        return value_payload(self._skills().mark_staged(workflow_key))

    def voice_inbox_prepare(self, *, transcript: str) -> dict[str, Any]:
        self._capabilities().require("memory.read")
        return value_payload(self._voice_vocabulary().prepare(transcript))

    def voice_vocabulary_add(self, *, spoken: str, canonical: str) -> dict[str, Any]:
        self._capabilities().require("memory.write")
        return value_payload(self._voice_vocabulary().add(spoken=spoken, canonical=canonical))

    def voice_vocabulary_list(self) -> dict[str, Any]:
        self._capabilities().require("memory.read")
        return {"items": value_payload(self._voice_vocabulary().list())}


def _message_plan_preview(plan: Any) -> str:
    return "\n".join(
        f"{index}. {item.contact_name}: {item.text} ({item.send_at.isoformat()})"
        for index, item in enumerate(plan.messages, start=1)
    )
