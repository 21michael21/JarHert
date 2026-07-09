from __future__ import annotations

import json
import re

from assistant.action_schema import ActionType, NaturalRoute, PlannedAction
from assistant.action_validator import validate_actions
from assistant.provider_clients import HermesClient
from assistant.provider_diagnostics import HermesClientError
from assistant.types import HermesRequest, Intent, UserContext


class LlmActionExtractor:
    def __init__(self, hermes: HermesClient) -> None:
        self.hermes = hermes

    def extract(self, user: UserContext, text: str) -> NaturalRoute:
        try:
            response = self.hermes.ask(
                HermesRequest(
                    user=user,
                    prompt=_build_prompt(text),
                    intent=Intent.ASK,
                    context={"mode": "action_extraction_json"},
                )
            )
        except HermesClientError:
            return NaturalRoute(actions=[], fallback_to_ai=True, reason="llm_unavailable")
        except Exception:
            return NaturalRoute(actions=[], fallback_to_ai=True, reason="llm_unavailable")

        data = _loads_json_object(response.text)
        if data is None:
            return NaturalRoute(actions=[], fallback_to_ai=True, reason="llm_invalid_json")
        actions = _planned_actions_from_payload(data)
        validation = validate_actions(actions)
        if validation.ok:
            return NaturalRoute(actions=validation.actions, fallback_to_ai=False, reason="llm_extracted")
        if validation.reason == "llm_low_confidence":
            return NaturalRoute(actions=[], fallback_to_ai=False, reason="llm_low_confidence")
        return NaturalRoute(actions=[], fallback_to_ai=True, reason=validation.reason)


def _build_prompt(text: str) -> str:
    return "\n".join(
        [
            "Ты извлекаешь действия из русского Telegram-сообщения.",
            "Верни только JSON без пояснений.",
            "Формат: {\"actions\":[{\"type\":\"task.create\",\"payload\":{\"title\":\"...\"},\"confidence\":0.0}]}",
            "Разрешенные type: idea.save, memory.save, reminder.create, task.create, task.list, task.move, task.done, calendar.create, calendar.move, agent.job.create.",
            "Если не уверен, ставь confidence ниже 0.75.",
            "Не исполняй действие. Только извлеки структурированный план.",
            f"Сообщение: {text}",
        ]
    )


def _loads_json_object(text: str) -> dict | None:
    value = (text or "").strip()
    fence = re.match(r"^```(?:json)?\s*(?P<body>.*?)\s*```$", value, re.IGNORECASE | re.DOTALL)
    if fence:
        value = fence.group("body").strip()
    if not value.startswith("{"):
        start = value.find("{")
        end = value.rfind("}")
        if start >= 0 and end > start:
            value = value[start : end + 1]
    try:
        data = json.loads(value)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _planned_actions_from_payload(data: dict) -> list[PlannedAction]:
    raw_actions = data.get("actions")
    if not isinstance(raw_actions, list):
        return []
    actions: list[PlannedAction] = []
    for item in raw_actions:
        if not isinstance(item, dict):
            continue
        try:
            action_type = ActionType(str(item.get("type") or ""))
        except ValueError:
            continue
        payload = item.get("payload") or {}
        if not isinstance(payload, dict):
            payload = {}
        confidence = _safe_float(item.get("confidence"), default=0.0)
        actions.append(
            PlannedAction(
                action_type,
                payload={str(key): str(value) for key, value in payload.items() if value is not None},
                confidence=confidence,
                needs_confirmation=bool(item.get("needs_confirmation") or False),
                reason="llm_extractor",
            )
        )
    return actions


def _safe_float(value: object, *, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
