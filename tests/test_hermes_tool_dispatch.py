import asyncio

import pytest

from hermes.native_tools.tool_dispatch import handler_parameter_contract, invoke_catalog_handler


def test_handler_contract_exposes_only_public_arguments() -> None:
    async def handler(title: str, count: int = 1, *, ctx: object) -> dict[str, object]:
        return {"title": title, "count": count}

    assert handler_parameter_contract(handler) == {
        "required": ["title"],
        "optional": ["count"],
        "requires_confirmation": True,
    }


def test_dispatcher_runs_discovered_handler_and_injects_context() -> None:
    context = object()

    async def handler(title: str, *, ctx: object) -> dict[str, object]:
        assert ctx is context
        return {"title": title}

    result = asyncio.run(
        invoke_catalog_handler({"task_create": handler}, name="task_create", payload={"title": "Проверить"}, ctx=context)
    )

    assert result == {"title": "Проверить"}


@pytest.mark.parametrize(
    ("payload", "message"),
    [({"title": "Проверить", "extra": "нет"}, "не принимает поля"), ({}, "нужны поля")],
)
def test_dispatcher_rejects_unexpected_and_missing_mutation_fields(payload: dict[str, str], message: str) -> None:
    def handler(title: str) -> dict[str, str]:
        return {"title": title}

    with pytest.raises(ValueError, match=message):
        asyncio.run(invoke_catalog_handler({"task_create": handler}, name="task_create", payload=payload, ctx=object()))


def test_dispatcher_does_not_dispatch_bootstrap_tools_recursively() -> None:
    with pytest.raises(ValueError, match="не найден"):
        asyncio.run(
            invoke_catalog_handler(
                {"tool_catalog_invoke": lambda: {}},
                name="tool_catalog_invoke",
                payload={},
                ctx=object(),
                forbidden_names=frozenset({"tool_catalog_invoke"}),
            )
        )


def test_dispatcher_validates_declared_field_types() -> None:
    def handler(limit: int) -> dict[str, int]:
        return {"limit": limit}

    with pytest.raises(ValueError, match="неверный формат"):
        asyncio.run(
            invoke_catalog_handler(
                {"task_list": handler}, name="task_list", payload={"limit": "не число"}, ctx=object()
            )
        )
