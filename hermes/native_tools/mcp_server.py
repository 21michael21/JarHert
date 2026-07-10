from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Callable

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from native_tools.mcp_api import NativeToolsAPI
else:
    from .mcp_api import NativeToolsAPI


ToolHandler = Callable[..., dict[str, Any]]
API = NativeToolsAPI()


def _plan_id_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {"plan_id": {"type": "integer", "minimum": 1}},
        "required": ["plan_id"],
        "additionalProperties": False,
    }


def _action_schema(action_type: str, required: list[str], optional: list[str] | None = None) -> dict[str, Any]:
    fields = {name: {"type": "string", "minLength": 1} for name in [*required, *(optional or [])]}
    if action_type == "calendar.create":
        fields["reminder_minutes"] = {"type": ["integer", "null"], "minimum": 0}
    return {
        "type": "object",
        "properties": {
            "type": {"const": action_type},
            "payload": {
                "type": "object",
                "properties": fields,
                "required": required,
                "additionalProperties": False,
            },
        },
        "required": ["type", "payload"],
        "additionalProperties": False,
    }


ACTION_TOOL_SCHEMAS = [
    _action_schema("task.create", ["title"], ["list_name", "project", "priority", "due", "description"]),
    _action_schema("task.move", ["title", "target_list"]),
    _action_schema("task.done", ["title"], ["summary"]),
    _action_schema("task.delete", ["title"]),
    _action_schema("calendar.create", ["title", "start", "end"], ["description"]),
    _action_schema("calendar.move", ["title", "start", "end"]),
    _action_schema("calendar.delete", ["title"]),
]


TOOLS: dict[str, dict[str, Any]] = {
    "integration_health": {
        "description": "Check whether Trello and Google Calendar adapters are ready.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    "task_list": {
        "description": "List Trello tasks, optionally from one list.",
        "inputSchema": {
            "type": "object",
            "properties": {"list_name": {"type": ["string", "null"]}},
            "additionalProperties": False,
        },
    },
    "calendar_list": {
        "description": "List Google Calendar events for today or tomorrow.",
        "inputSchema": {
            "type": "object",
            "properties": {"when": {"type": "string", "default": "today"}},
            "additionalProperties": False,
        },
    },
    "action_plan_create": {
        "description": "FIRST create an idempotent mutation plan using only the exact action types in this schema; THEN ask for one user confirmation.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "actions": {"type": "array", "minItems": 1, "maxItems": 20, "items": {"oneOf": ACTION_TOOL_SCHEMAS}},
                "idempotency_key": {"type": "string", "minLength": 1},
            },
            "required": ["actions", "idempotency_key"],
            "additionalProperties": False,
        },
    },
    "action_plan_execute": {
        "description": "After the user pressed the confirmation button, atomically approve and execute the plan exactly once.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "plan_id": {"type": "integer", "minimum": 1},
                "confirmed": {"const": True},
            },
            "required": ["plan_id", "confirmed"],
            "additionalProperties": False,
        },
    },
    "action_plan_cancel": {
        "description": "Cancel a draft mutation plan.",
        "inputSchema": _plan_id_schema(),
    },
    "telegram_text_export": {
        "description": "Export text-only history from an owner-accessible Telegram dialog after one confirmation.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "peer": {"type": "string"},
                "output_format": {"type": "string", "enum": ["txt", "jsonl"], "default": "txt"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50000, "default": 5000},
                "confirmed": {"type": "boolean"},
            },
            "required": ["peer", "confirmed"],
            "additionalProperties": False,
        },
    },
}


def dispatch_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    handlers: dict[str, ToolHandler] = {
        "integration_health": API.integration_health,
        "task_list": API.task_list,
        "calendar_list": API.calendar_list,
        "action_plan_create": API.action_plan_create,
        "action_plan_execute": API.action_plan_execute,
        "action_plan_cancel": API.action_plan_cancel,
        "telegram_text_export": API.telegram_text_export,
    }
    if name not in handlers:
        raise ValueError(f"Unknown native tool: {name}")
    return handlers[name](**arguments)


def handle_message(message: dict[str, Any]) -> dict[str, Any] | None:
    request_id = message.get("id")
    method = message.get("method")
    if request_id is None:
        return None
    if method == "initialize":
        version = (message.get("params") or {}).get("protocolVersion") or "2024-11-05"
        return _result(request_id, {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "jarhert-native", "version": "0.1.0"},
        })
    if method == "ping":
        return _result(request_id, {})
    if method == "tools/list":
        return _result(request_id, {"tools": [{"name": name, **definition} for name, definition in TOOLS.items()]})
    if method == "tools/call":
        params = message.get("params") or {}
        try:
            payload = dispatch_tool(str(params.get("name") or ""), dict(params.get("arguments") or {}))
        except Exception as error:
            text = _bounded(str(error) or type(error).__name__, 500)
            return _result(request_id, {"content": [{"type": "text", "text": text}], "isError": True})
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        return _result(request_id, {"content": [{"type": "text", "text": text}], "isError": False})
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "Method not found"}}


def _result(request_id: Any, payload: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": payload}


def _bounded(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


def main() -> int:
    for raw_line in sys.stdin:
        try:
            message = json.loads(raw_line)
            response = handle_message(message)
        except Exception as error:
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32603, "message": _bounded(str(error), 300)}}
        if response is not None:
            print(json.dumps(response, ensure_ascii=False, separators=(",", ":")), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
