"""Small, strict dispatcher for the discovery-first MCP surface.

Hermes discovers MCP tools when a conversation starts.  Keeping every native
tool in that initial prompt makes a personal profile slower and less reliable.
This module lets the small bootstrap surface dispatch to a catalogued handler
only after the model has discovered its exact contract.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import uuid
from collections.abc import Callable, Mapping
from typing import Any, get_type_hints

from pydantic import TypeAdapter, ValidationError

from .events import EventStore


def handler_parameter_contract(handler: Callable[..., object]) -> dict[str, object]:
    """Describe only public arguments; ``ctx`` is injected by the MCP runtime."""
    required: list[str] = []
    optional: list[str] = []
    needs_confirmation = False
    for parameter in inspect.signature(handler).parameters.values():
        if parameter.name == "ctx":
            needs_confirmation = True
            continue
        if parameter.kind in {parameter.VAR_POSITIONAL, parameter.VAR_KEYWORD}:
            continue
        if parameter.default is inspect.Parameter.empty:
            required.append(parameter.name)
        else:
            optional.append(parameter.name)
    return {
        "required": required,
        "optional": optional,
        "requires_confirmation": needs_confirmation,
    }


async def invoke_catalog_handler(
    handlers: Mapping[str, Callable[..., object]],
    *,
    name: str,
    payload: Mapping[str, Any] | None,
    ctx: object,
    forbidden_names: frozenset[str] = frozenset(),
    event_store: EventStore | None = None,
) -> object:
    """Call one registered handler while rejecting unknown and missing fields."""
    normalized_name = str(name or "").strip()
    handler = handlers.get(normalized_name)
    if handler is None or normalized_name in forbidden_names:
        raise ValueError("Инструмент не найден в каталоге.")
    if payload is None:
        values: dict[str, Any] = {}
    elif isinstance(payload, Mapping):
        values = dict(payload)
    else:
        raise ValueError("payload должен быть JSON-объектом.")

    signature = inspect.signature(handler)
    public_parameters = {
        parameter.name: parameter
        for parameter in signature.parameters.values()
        if parameter.name != "ctx"
        and parameter.kind not in {parameter.VAR_POSITIONAL, parameter.VAR_KEYWORD}
    }
    unknown = sorted(set(values) - set(public_parameters))
    if unknown:
        raise ValueError(f"Инструмент {name} не принимает поля: {', '.join(unknown)}.")
    missing = sorted(
        parameter.name
        for parameter in public_parameters.values()
        if parameter.default is inspect.Parameter.empty and parameter.name not in values
    )
    if missing:
        raise ValueError(f"Для {name} нужны поля: {', '.join(missing)}.")

    annotations = get_type_hints(handler, include_extras=True)
    for field, value in tuple(values.items()):
        annotation = annotations.get(field, Any)
        try:
            values[field] = TypeAdapter(annotation).validate_python(value)
        except ValidationError as error:
            raise ValueError(f"Поле {field} для {name} имеет неверный формат.") from error

    if "ctx" in signature.parameters:
        values["ctx"] = ctx
    public_values = {key: value for key, value in values.items() if key != "ctx"}
    invocation_id = uuid.uuid4().hex
    payload_summary = _payload_summary(public_values)
    _record_event(
        event_store,
        "tool.invoke_started",
        {"invocation_id": invocation_id, "tool": name, **payload_summary},
    )
    try:
        result = handler(**values)
        if inspect.isawaitable(result):
            result = await result
    except Exception as error:
        _record_event(
            event_store,
            "tool.invoke_failed",
            {
                "invocation_id": invocation_id,
                "tool": name,
                **payload_summary,
                "error": str(error)[:500] or error.__class__.__name__,
            },
        )
        raise
    _record_event(
        event_store,
        "tool.invoke_succeeded",
        {"invocation_id": invocation_id, "tool": name, **payload_summary},
    )
    return result


def _payload_summary(values: Mapping[str, Any]) -> dict[str, object]:
    return {
        "payload_keys": sorted(str(key) for key in values),
        "payload_hash": hashlib.sha256(
            json.dumps(values, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest(),
    }


def _record_event(event_store: EventStore | None, event_type: str, payload: dict[str, object]) -> None:
    if event_store is None:
        return
    try:
        event_store.record(event_type, "tool_dispatch", payload)
    except Exception:
        return
