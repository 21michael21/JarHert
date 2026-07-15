"""Small, strict dispatcher for the discovery-first MCP surface.

Hermes discovers MCP tools when a conversation starts.  Keeping every native
tool in that initial prompt makes a personal profile slower and less reliable.
This module lets the small bootstrap surface dispatch to a catalogued handler
only after the model has discovered its exact contract.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping
from typing import Any, get_type_hints

from pydantic import TypeAdapter, ValidationError


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
) -> object:
    """Call one registered handler while rejecting unknown and missing fields."""
    handler = handlers.get(str(name or "").strip())
    if handler is None or name in forbidden_names:
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
    result = handler(**values)
    if inspect.isawaitable(result):
        return await result
    return result
