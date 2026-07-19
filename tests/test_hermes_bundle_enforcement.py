from __future__ import annotations

from pathlib import Path

import pytest

from hermes.native_tools.mcp_api import NativeToolsAPI
from hermes.native_tools.tool_catalog import tool_is_active, tool_spec


def test_discovery_hides_tools_from_disabled_bundles(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HERMES_TOOL_BUNDLES", "personal,planning")
    api = NativeToolsAPI(database_path=tmp_path / "personal.sqlite3")

    result = api.tool_catalog_discover(query="", limit=12)

    names = {item["name"] for item in result["items"]}
    assert "coding_job_list" not in names
    assert "github_repo_create_confirmed" not in names
    assert not tool_is_active(tool_spec("coding_job_list"), "personal,planning")


def test_discovery_allows_operations_bootstrap_in_any_bundle_selection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HERMES_TOOL_BUNDLES", "personal")
    api = NativeToolsAPI(database_path=tmp_path / "personal.sqlite3")

    result = api.tool_catalog_discover(query="system_status", limit=12)

    names = {item["name"] for item in result["items"]}
    assert "system_status" in names
    assert tool_is_active(tool_spec("system_status"), "personal")
