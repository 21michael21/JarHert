from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import subprocess
import sys

from hermes.native_tools.tool_catalog import (
    TOOL_CATALOG,
    ToolBundle,
    active_tool_bundles,
    configured_tool_names,
    tool_names_for_active_bundles,
    validate_tool_catalog,
)


ROOT = Path(__file__).resolve().parents[1]


def test_catalog_is_the_complete_contract_for_configured_native_tools() -> None:
    configured = configured_tool_names(ROOT / "hermes" / "config.yaml")
    catalogued = {spec.name for spec in TOOL_CATALOG if spec.enabled_by_default}

    assert configured == catalogued


def test_catalog_covers_every_registered_mcp_tool_and_its_api_handler() -> None:
    runtime_path = ROOT / "hermes" / "native_tools" / "mcp_runtime.py"
    runtime_source = runtime_path.read_text(encoding="utf-8")
    assert "@native_tool()" in runtime_source
    runtime = ast.parse(runtime_source)
    registered = {
        node.name
        for node in runtime.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and any(
            isinstance(decorator, ast.Call)
            and (
                (isinstance(decorator.func, ast.Attribute) and decorator.func.attr == "tool")
                or (isinstance(decorator.func, ast.Name) and decorator.func.id == "native_tool")
            )
            for decorator in node.decorator_list
        )
    }
    catalogued = {spec.name for spec in TOOL_CATALOG}

    assert registered == catalogued
    assert validate_tool_catalog() == []

    handlers_by_tool = {}
    for node in runtime.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or node.name not in catalogued:
            continue
        handlers_by_tool[node.name] = {
            part.func.attr
            for part in ast.walk(node)
            if isinstance(part, ast.Call)
            and isinstance(part.func, ast.Attribute)
            and isinstance(part.func.value, ast.Name)
            and part.func.value.id == "api"
        }
    for spec in TOOL_CATALOG:
        assert spec.handler in handlers_by_tool[spec.name]


def test_catalog_assigns_every_tool_to_one_user_facing_bundle() -> None:
    assert {spec.bundle for spec in TOOL_CATALOG} == set(ToolBundle)
    assert all(spec.handler and spec.risk in {"low", "medium", "high"} for spec in TOOL_CATALOG)


def test_bundle_selection_keeps_operations_and_hides_unrelated_tools() -> None:
    selected = active_tool_bundles("research, code")

    assert selected == {ToolBundle.OPERATIONS, ToolBundle.RESEARCH, ToolBundle.CODE}
    tools = set(tool_names_for_active_bundles("research, code"))
    assert {"system_status", "knowledge_search", "coding_job_list"} <= tools
    assert "shopping_list" not in tools


def test_all_bundle_preserves_the_complete_native_tool_surface() -> None:
    assert set(tool_names_for_active_bundles("all")) == {spec.name for spec in TOOL_CATALOG}


def test_runtime_registers_only_the_selected_tool_bundles() -> None:
    script = """
import json
import sys
import types

class Context:
    pass

class FastMCP:
    def __init__(self, _name):
        self.names = []
    def tool(self, *, name):
        def register(function):
            self.names.append(name)
            return function
        return register

mcp = types.ModuleType('mcp')
server = types.ModuleType('mcp.server')
fastmcp = types.ModuleType('mcp.server.fastmcp')
fastmcp.Context = Context
fastmcp.FastMCP = FastMCP
sys.modules['mcp'] = mcp
sys.modules['mcp.server'] = server
sys.modules['mcp.server.fastmcp'] = fastmcp

from hermes.native_tools.mcp_runtime import mcp as runtime
print(json.dumps(runtime.names))
"""
    environment = {**os.environ, "HERMES_TOOL_BUNDLES": "research,code"}
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )

    registered = set(json.loads(result.stdout))
    assert {"system_status", "knowledge_search", "coding_job_list"} <= registered
    assert "shopping_list" not in registered
