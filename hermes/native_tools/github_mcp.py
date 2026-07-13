"""Configuration-only health for the optional official GitHub MCP server."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping


GITHUB_READ_ONLY_TOOLSETS = (
    "repos",
    "issues",
    "pull_requests",
    "actions",
    "users",
    "code_security",
)


def github_mcp_status(
    *,
    profile_home: str | Path,
    environ: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """Return a safe readiness state without starting the MCP server or exposing a token."""
    values = os.environ if environ is None else environ
    enabled = _enabled(values.get("GITHUB_MCP_ENABLED", ""))
    common = {"enabled": enabled, "read_only": True, "toolsets": list(GITHUB_READ_ONLY_TOOLSETS)}
    if not enabled:
        return {"state": "disabled", **common}
    if not values.get("GITHUB_PERSONAL_ACCESS_TOKEN", "").strip():
        return {"state": "needs_token", **common}
    configured = values.get("GITHUB_MCP_BINARY", "").strip()
    binary = Path(configured).expanduser() if configured else Path(profile_home).expanduser() / "bin" / "github-mcp-server"
    if not binary.is_file() or not os.access(binary, os.X_OK):
        return {"state": "missing_binary", **common}
    return {"state": "ready", **common}


def _enabled(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}
