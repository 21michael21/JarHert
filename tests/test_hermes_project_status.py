from __future__ import annotations

from pathlib import Path

from hermes.native_tools.mcp_api import NativeToolsAPI


class _Adapter:
    def list_tasks(self, *, list_name: str | None = None) -> str:
        return "- Задача А [open] | list: Today\n- Задача Б [open] | list: Later"


class _BrokenAdapter:
    def list_tasks(self, *, list_name: str | None = None) -> str:
        raise RuntimeError("Trello down")


def _seed(api: NativeToolsAPI) -> None:
    api._personal_os().upsert_project(key="NoManual", name="NoManual", context_note="онбординг кураторов")
    api._personal_os().upsert_memory_block(block_type="note", subject="NoManual", content="Договорились о пилоте", project="NoManual")
    api._personal_os().create_commitment(
        subject="Илья",
        content="Прислать список кураторов",
        project="NoManual",
        due_at="2999-01-10T10:00:00+03:00",
    )
    api._crm().log_interaction(contact="Илья", kind="meeting", summary="Обсудили пилот", project="NoManual", idempotency_key="crm1")


def test_project_status_report_collects_project_snapshot(tmp_path: Path) -> None:
    api = NativeToolsAPI(database_path=tmp_path / "personal.sqlite3", adapter_factory=_Adapter)
    _seed(api)

    report = api.project_status_report(project="nomanual")

    assert report["project"] == "NoManual"
    assert report["resolved_from_memory"] is True
    assert report["open_commitments"][0]["subject"] == "Илья"
    assert report["notes"][0]["project"] == "NoManual"
    assert report["recent_interactions"][0]["summary"] == "Обсудили пилот"
    assert "Задача А" in report["open_tasks_text"]
    assert report["adapter_error"] is None


def test_project_status_report_survives_adapter_outage(tmp_path: Path) -> None:
    api = NativeToolsAPI(database_path=tmp_path / "personal.sqlite3", adapter_factory=_BrokenAdapter)
    _seed(api)

    report = api.project_status_report(project="NoManual")

    assert report["open_tasks_text"] == ""
    assert report["adapter_error"] == "Trello down"
    assert report["notes"]


def test_project_status_report_rejects_empty_project(tmp_path: Path) -> None:
    api = NativeToolsAPI(database_path=tmp_path / "personal.sqlite3")

    try:
        api.project_status_report(project="  ")
    except ValueError as error:
        assert "проект" in str(error).lower()
    else:  # pragma: no cover
        raise AssertionError("empty project accepted")
