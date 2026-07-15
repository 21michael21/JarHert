from __future__ import annotations

import hashlib
import hmac
import json
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlencode

from fastapi.testclient import TestClient

from hermes.native_tools.dashboard import DashboardSettings, create_app
from hermes.native_tools.dashboard_read_model import build_dashboard_snapshot
from hermes.scripts.configure_dashboard_menu_button import configure_menu_button


BOT_TOKEN = "123456:dashboard-test-token"
OWNER_ID = 566055009


class FakeDashboardAPI:
    def __init__(self) -> None:
        self.rescheduled: list[dict[str, object]] = []
        self.cancelled: list[int] = []
        self.edited_notes: list[dict[str, object]] = []
        self.deleted_notes: list[int] = []
        self.created_plans: list[dict[str, object]] = []
        self.executed_plans: list[int] = []
        self.archived_urls: list[dict[str, object]] = []
        self.monitor_schedules: list[dict[str, object]] = []
        self.coding_requests: list[dict[str, object]] = []

    def personal_today(self):
        return {
            "tasks": "- Проверить OAuth [open] | list: Today | https://trello.com/c/example\n- Собрать план [open] | list: Today",
            "calendar": "15:00 Созвон",
            "top_three": [{"title": "Проверить OAuth"}],
        }

    def system_status(self):
        return {
            "gateway": {"active": True},
            "resources": {"disk_free_gib": 68},
            "backup": {"configured": True},
        }

    def reminder_list(self, **_kwargs):
        return {"items": [{"id": 7, "text": "Позвонить врачу", "remind_at": "2026-07-13T12:00:00+03:00"}]}

    def reminder_reschedule(self, **payload):
        self.rescheduled.append(payload)
        return {"id": payload["reminder_id"], "text": "Позвонить врачу", "remind_at": payload["remind_at"]}

    def reminder_cancel(self, *, reminder_id: int):
        self.cancelled.append(reminder_id)
        return {"id": reminder_id, "status": "cancelled"}

    def memory_block_list(self, **_kwargs):
        return {"items": [{"id": 4, "subject": "OAuth", "content": "Проверить callback", "project": "Hub_ML"}]}

    def note_edit(self, *, note_id: int, content: str):
        self.edited_notes.append({"note_id": note_id, "content": content})
        return {"id": note_id, "subject": "OAuth", "content": content, "project": "Hub_ML"}

    def note_delete(self, *, note_id: int):
        self.deleted_notes.append(note_id)
        return {"id": note_id, "status": "deleted"}

    def monitor_list(self):
        return {
            "items": [
                {
                    "id": 5,
                    "name": "GitHub",
                    "enabled": True,
                    "source_config": {"quiet_hours": "23:00-08:00", "timezone": "Europe/Moscow"},
                }
            ]
        }

    def monitor_digest(self):
        return {"items": [{"monitor": "GitHub"}], "item_ids": [4]}

    def monitor_schedule_update(self, *, monitor_id: int, quiet_hours: str | None, timezone_name: str):
        payload = {"monitor_id": monitor_id, "quiet_hours": quiet_hours, "timezone_name": timezone_name}
        self.monitor_schedules.append(payload)
        return {
            "id": monitor_id,
            "name": "GitHub",
            "enabled": True,
            "source_config": {
                "quiet_hours": quiet_hours,
                "timezone": timezone_name,
            },
        }

    def project_context_list(self):
        return {"items": [{"name": "Hub_ML"}]}

    def integration_health(self):
        return {"trello_ok": True, "calendar_ok": True}

    def work_mode_get(self):
        return {"mode": "fast"}

    def task_dashboard(self):
        return {
            "items": [{"title": "Проверить OAuth", "list_name": "Today", "priority": "P1", "url": "https://trello.com/c/example"}],
            "lists": ["Inbox", "Today", "Done"],
            "priorities": ["P1", "P2", "P3"],
            "board_url": "https://trello.com/b/example",
        }

    def calendar_dashboard(self, *, days: int = 7):
        assert days == 7
        return {"items": [{"title": "Созвон", "start": "2026-07-13T15:00:00+03:00", "end": "2026-07-13T16:00:00+03:00", "url": "https://calendar.google.com/calendar/event?eid=abc"}]}

    def action_plan_create(self, *, actions, idempotency_key: str):
        self.created_plans.append({"actions": actions, "idempotency_key": idempotency_key})
        return {"id": 41, "status": "draft", "idempotency_key": idempotency_key, "actions": actions}

    def action_plan_execute(self, *, plan_id: int, confirmed: bool):
        assert confirmed is True
        self.executed_plans.append(plan_id)
        return {"id": plan_id, "status": "succeeded", "actions": []}

    def action_plan_cancel(self, *, plan_id: int):
        return {"id": plan_id, "status": "cancelled", "actions": []}

    def coding_job_list(self, *, limit: int = 20, include_result: bool = False):
        assert limit == 20
        assert include_result is True
        return {
            "items": [
                {
                    "id": 12,
                    "mode": "coding",
                    "prompt": "Убрать лишний рендер PDF страницы",
                    "repository_url": "https://github.com/example/reader",
                    "status": "succeeded",
                    "result_text": "Нашёл лишний рендер. 14 тестов прошли.",
                    "last_error": None,
                }
            ]
        }

    def coding_job_enqueue(
        self,
        *,
        mode: str,
        prompt: str,
        idempotency_key: str,
        repository_url: str | None = None,
        source_urls: list[str] | None = None,
    ):
        self.coding_requests.append(
            {
                "mode": mode,
                "prompt": prompt,
                "idempotency_key": idempotency_key,
                "repository_url": repository_url,
                "source_urls": source_urls or [],
            }
        )
        return {
            "id": 22,
            "mode": mode,
            "prompt": prompt,
            "repository_url": repository_url,
            "source_urls": source_urls or [],
            "status": "queued",
            "idempotency_key": idempotency_key,
        }

    def note_search(self, *, query: str, project: str | None = None, limit: int = 50):
        assert query == "OAuth"
        assert project is None
        assert limit == 50
        return {"items": self.memory_block_list()["items"]}

    def note_history(self, *, note_id: int):
        assert note_id == 4
        return {"items": [{"id": 3, "note_id": 4, "content": "Старый текст", "changed_at": "2026-07-11T12:00:00+00:00"}]}

    def knowledge_list_sources(self, *, project: str | None = None, limit: int = 50):
        assert project is None
        assert limit == 50
        return {"items": [{"id": 2, "url": "https://docs.example.test/oauth", "title": "OAuth guide", "project": "Hub_ML", "snapshot_count": 2}]}

    def knowledge_archive_url(self, *, url: str, project: str | None = None):
        payload = {"url": url, "project": project}
        self.archived_urls.append(payload)
        return {"id": 9, "url": url, "title": "Saved page", "project": project, "snapshot_count": 1, "changed": True}

    def subscription_list(self, *, status: str = "active"):
        assert status == "active"
        return {
            "items": [{"id": 8, "name": "VDS", "amount": "20", "currency": "USD", "next_charge_at": "2026-07-15T09:00:00+00:00"}],
            "monthly_totals": {"USD": "20"},
        }


def test_dashboard_read_model_is_independent_from_http_routes() -> None:
    snapshot = build_dashboard_snapshot(FakeDashboardAPI())

    assert snapshot["today"]["priorities"] == [{"title": "Проверить OAuth"}]
    assert snapshot["today"]["tasks"] == ["Проверить OAuth", "Собрать план"]
    assert snapshot["integrations"] == {"trello_ok": True, "calendar_ok": True}


def _init_data(*, user_id: int = OWNER_ID, auth_date: int | None = None, tamper: bool = False) -> str:
    values = {
        "auth_date": str(auth_date or int(time.time())),
        "query_id": "AAH-test",
        "user": json.dumps({"id": user_id, "first_name": "Миша"}, ensure_ascii=False, separators=(",", ":")),
    }
    check_string = "\n".join(f"{key}={values[key]}" for key in sorted(values))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode("utf-8"), hashlib.sha256).digest()
    values["hash"] = hmac.new(secret, check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    if tamper:
        values["hash"] = "0" * 64
    return urlencode(values)


def client(api: FakeDashboardAPI | None = None) -> tuple[TestClient, FakeDashboardAPI]:
    dashboard_api = api or FakeDashboardAPI()
    app = create_app(
        api=dashboard_api,
        settings=DashboardSettings(
            bot_token=BOT_TOKEN,
            session_secret="test-secret",
            allowed_user_ids=frozenset({OWNER_ID}),
            secure_cookie=False,
        ),
    )
    return TestClient(app), dashboard_api


def sign_in(app_client: TestClient) -> None:
    response = app_client.post("/api/session/telegram", json={"init_data": _init_data()})
    assert response.status_code == 200
    assert response.json() == {"ok": True, "user_id": OWNER_ID}


def test_dashboard_rejects_tampered_stale_and_unallowed_telegram_data() -> None:
    app_client, _ = client()

    assert app_client.post("/api/session/telegram", json={"init_data": _init_data(tamper=True)}).status_code == 401
    assert app_client.post(
        "/api/session/telegram",
        json={"init_data": _init_data(auth_date=int(time.time()) - 3700)},
    ).status_code == 401
    assert app_client.post("/api/session/telegram", json={"init_data": _init_data(user_id=999)}).status_code == 403


def test_dashboard_telegram_session_exposes_only_safe_personal_snapshot() -> None:
    app_client, _ = client()

    assert app_client.get("/api/snapshot").status_code == 401
    sign_in(app_client)
    snapshot = app_client.get("/api/snapshot")

    assert snapshot.status_code == 200
    payload = snapshot.json()
    assert payload["today"]["tasks"] == ["Проверить OAuth", "Собрать план"]
    assert payload["today"]["calendar"] == ["15:00 Созвон"]
    assert payload["notes"] == [{"id": 4, "subject": "OAuth", "content": "Проверить callback", "project": "Hub_ML"}]
    assert "https://" not in str(payload)


def test_dashboard_uses_trello_tasks_as_priorities_when_personal_queue_is_empty() -> None:
    class EmptyPersonalQueue(FakeDashboardAPI):
        def personal_today(self):
            data = super().personal_today()
            data["top_three"] = []
            return data

    app_client, _ = client(EmptyPersonalQueue())
    sign_in(app_client)
    snapshot = app_client.get("/api/snapshot").json()

    assert [item["title"] for item in snapshot["today"]["priorities"]] == ["Проверить OAuth", "Собрать план"]


def test_dashboard_allows_explicit_reminder_correction_and_note_edit() -> None:
    app_client, dashboard_api = client()
    sign_in(app_client)

    moved = app_client.post(
        "/api/reminders/7/reschedule",
        json={"remind_at": "2026-07-14T19:00:00+03:00", "recurrence": "keep"},
    )
    cancelled = app_client.post("/api/reminders/7/cancel")
    note = app_client.put("/api/notes/4", json={"content": "Проверить callback и redirect URI"})

    assert moved.status_code == 200
    assert cancelled.status_code == 200
    assert note.status_code == 200
    assert dashboard_api.rescheduled == [{"reminder_id": 7, "remind_at": "2026-07-14T19:00:00+03:00", "recurrence": "keep"}]
    assert dashboard_api.cancelled == [7]
    assert dashboard_api.edited_notes == [{"note_id": 4, "content": "Проверить callback и redirect URI"}]


def test_dashboard_does_not_mutate_personal_data_without_telegram_session() -> None:
    app_client, dashboard_api = client()

    response = app_client.post(
        "/api/reminders/7/reschedule",
        json={"remind_at": "2026-07-14T19:00:00+03:00"},
    )

    assert response.status_code == 401
    assert dashboard_api.rescheduled == []


def test_dashboard_exposes_operational_trello_and_calendar_views() -> None:
    app_client, _ = client()
    sign_in(app_client)

    tasks = app_client.get("/api/tasks")
    calendar = app_client.get("/api/calendar")

    assert tasks.status_code == 200
    assert tasks.json()["lists"] == ["Inbox", "Today", "Done"]
    assert tasks.json()["items"][0]["priority"] == "P1"
    assert tasks.json()["board_url"] == "https://trello.com/b/example"
    assert calendar.status_code == 200
    assert calendar.json()["items"][0]["title"] == "Созвон"


def test_dashboard_exposes_code_desk_notes_and_saved_knowledge() -> None:
    app_client, _ = client()
    sign_in(app_client)

    coding = app_client.get("/api/coding/jobs")
    notes = app_client.get("/api/notes", params={"query": "OAuth"})
    history = app_client.get("/api/notes/4/history")
    knowledge = app_client.get("/api/knowledge/sources")

    assert coding.status_code == 200
    assert coding.json()["items"][0]["status"] == "succeeded"
    assert notes.status_code == 200
    assert notes.json()["items"][0]["subject"] == "OAuth"
    assert history.status_code == 200
    assert history.json()["items"][0]["content"] == "Старый текст"
    assert knowledge.status_code == 200
    assert knowledge.json()["items"][0]["snapshot_count"] == 2


def test_dashboard_exposes_personal_radar_without_calendar_mutations() -> None:
    app_client, _ = client()
    sign_in(app_client)

    radar = app_client.get("/api/subscriptions")

    assert radar.status_code == 200
    assert radar.json()["items"] == [
        {"id": 8, "name": "VDS", "amount": "20", "currency": "USD", "next_charge_at": "2026-07-15T09:00:00+00:00"}
    ]


def test_dashboard_radar_exposes_digest_and_changes_quiet_hours_only_after_session(tmp_path) -> None:
    app_client, dashboard_api = client()
    sign_in(app_client)

    digest = app_client.get("/api/monitors/digest")
    changed = app_client.put(
        "/api/monitors/5/schedule",
        json={"quiet_hours": "22:30-08:30", "timezone": "Europe/Moscow"},
    )

    assert digest.status_code == 200
    assert digest.json()["item_ids"] == [4]
    assert changed.status_code == 200
    assert dashboard_api.monitor_schedules == [
        {"monitor_id": 5, "quiet_hours": "22:30-08:30", "timezone_name": "Europe/Moscow"}
    ]

    blocked_client, blocked_api = client()
    assert blocked_client.put("/api/monitors/5/schedule", json={"quiet_hours": "22:30-08:30"}).status_code == 401
    assert blocked_api.monitor_schedules == []


def test_dashboard_archives_knowledge_clip_only_after_its_preview_token() -> None:
    app_client, dashboard_api = client()
    sign_in(app_client)
    payload = {
        "request_id": "knowledge-clip-001",
        "url": "https://docs.example.test/oauth",
        "project": "Hub_ML",
    }

    preview = app_client.post("/api/knowledge/clips/preview", json=payload)
    blocked = app_client.post("/api/knowledge/clips/execute", json=payload)
    saved = app_client.post(
        "/api/knowledge/clips/execute",
        json={**payload, "clip_token": preview.json()["clip_token"]},
    )

    assert preview.status_code == 200
    assert preview.json()["preview"] == ["Сохранить страницу в базу знаний", "Проект: Hub_ML"]
    assert blocked.status_code == 403
    assert saved.status_code == 200
    assert dashboard_api.archived_urls == [{"url": "https://docs.example.test/oauth", "project": "Hub_ML"}]


def test_dashboard_personal_views_require_a_telegram_session() -> None:
    app_client, _ = client()

    for path in ("/api/coding/jobs", "/api/notes", "/api/notes/4/history", "/api/knowledge/sources", "/api/subscriptions"):
        assert app_client.get(path).status_code == 401


def test_dashboard_creates_one_preview_plan_then_executes_only_after_explicit_confirmation() -> None:
    app_client, dashboard_api = client()
    sign_in(app_client)

    draft = app_client.post(
        "/api/plans",
        json={
            "request_id": "quick-add-task-001",
            "actions": [{"type": "task.create", "payload": {"title": "Проверить Mini App", "list_name": "Today", "priority": "P1"}}],
        },
    )
    assert draft.status_code == 200
    token = draft.json()["plan_token"]
    blocked = app_client.post("/api/plans/41/execute", json={})
    executed = app_client.post("/api/plans/41/execute", json={"plan_token": token})

    assert draft.json()["preview"] == ["Создать задачу: Проверить Mini App"]
    assert blocked.status_code == 403
    assert executed.status_code == 200
    assert dashboard_api.created_plans[0]["idempotency_key"].startswith(f"dashboard:{OWNER_ID}:quick-add-task-001")
    assert dashboard_api.executed_plans == [41]


def test_dashboard_previews_coding_job_before_adding_it_to_the_queue() -> None:
    app_client, dashboard_api = client()
    sign_in(app_client)
    payload = {
        "request_id": "coding-task-001",
        "mode": "coding",
        "prompt": "PDF reader hangs while turning pages",
        "repository_url": "https://github.com/example/reader",
    }

    preview = app_client.post("/api/coding/jobs/preview", json=payload)
    blocked = app_client.post("/api/coding/jobs/execute", json=payload)
    queued = app_client.post(
        "/api/coding/jobs/execute",
        json={**payload, "coding_token": preview.json()["coding_token"]},
    )

    assert preview.status_code == 200
    assert preview.json()["preview"] == [
        "Поставить кодовую задачу в очередь",
        "Репозиторий: https://github.com/example/reader",
        "Runner работает в sandbox и ничего не деплоит.",
    ]
    assert blocked.status_code == 403
    assert queued.status_code == 200
    assert queued.json()["status"] == "queued"
    assert queued.json()["idempotency_key"].startswith(f"dashboard:coding:{OWNER_ID}:coding-task-001")
    assert dashboard_api.coding_requests == [
        {
            "mode": "coding",
            "prompt": "PDF reader hangs while turning pages",
            "idempotency_key": f"dashboard:coding:{OWNER_ID}:coding-task-001",
            "repository_url": "https://github.com/example/reader",
            "source_urls": [],
        }
    ]


def test_dashboard_previews_source_bounded_hypothesis_before_queueing_it() -> None:
    app_client, dashboard_api = client()
    sign_in(app_client)
    payload = {
        "request_id": "research-task-001",
        "mode": "research",
        "prompt": "Проверь гипотезу о причинах тормозов PDF",
        "source_urls": ["https://github.com/example/reader/issues"],
    }

    preview = app_client.post("/api/coding/jobs/preview", json=payload)
    queued = app_client.post(
        "/api/coding/jobs/execute",
        json={**payload, "coding_token": preview.json()["coding_token"]},
    )

    assert preview.status_code == 200
    assert preview.json()["preview"] == [
        "Проверить гипотезу по источникам",
        "Источники: 1",
        "Runner работает в sandbox и ничего не деплоит.",
    ]
    assert queued.status_code == 200
    assert dashboard_api.coding_requests[0]["mode"] == "research"
    assert dashboard_api.coding_requests[0]["source_urls"] == ["https://github.com/example/reader/issues"]


def test_dashboard_deletes_note_only_for_an_authenticated_telegram_session() -> None:
    app_client, dashboard_api = client()

    assert app_client.delete("/api/notes/4").status_code == 401
    sign_in(app_client)
    deleted = app_client.delete("/api/notes/4")

    assert deleted.status_code == 200
    assert deleted.json() == {"id": 4, "status": "deleted"}
    assert dashboard_api.deleted_notes == [4]


def test_dashboard_cancel_requires_the_plan_token() -> None:
    app_client, _ = client()
    sign_in(app_client)
    draft = app_client.post(
        "/api/plans",
        json={
            "request_id": "quick-cancel-001",
            "actions": [{"type": "task.done", "payload": {"title": "Не выполнять"}}],
        },
    )

    assert app_client.post("/api/plans/41/cancel", json={}).status_code == 403
    cancelled = app_client.post("/api/plans/41/cancel", json={"plan_token": draft.json()["plan_token"]})

    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"


def test_dashboard_never_creates_external_plan_without_telegram_session() -> None:
    app_client, dashboard_api = client()

    response = app_client.post(
        "/api/plans",
        json={"request_id": "blocked-001", "actions": [{"type": "task.done", "payload": {"title": "Не трогать"}}]},
    )

    assert response.status_code == 401
    assert dashboard_api.created_plans == []


def test_dashboard_page_uses_telegram_webapp_and_external_assets_only() -> None:
    app_client, _ = client()

    response = app_client.get("/")

    assert response.status_code == 200
    assert 'src="https://telegram.org/js/telegram-web-app.js?62"' in response.text
    assert 'href="/assets/dashboard.css"' in response.text
    assert 'src="/assets/dashboard.js"' in response.text
    assert "<script>" not in response.text
    assert "script-src 'self' https://telegram.org" in response.headers["content-security-policy"]
    assert 'id="quick-add"' in response.text
    assert 'id="plan-dialog"' in response.text
    assert 'id="view-code"' in response.text
    assert 'id="note-search"' in response.text
    assert 'id="knowledge-sources"' in response.text


def test_dashboard_has_a_touch_first_command_center_and_preserves_navigation_state() -> None:
    app_client, _ = client()

    page = app_client.get("/").text
    script = (Path(__file__).parents[1] / "hermes" / "native_tools" / "dashboard_assets" / "dashboard.js").read_text()

    assert 'id="overview-tasks"' in page
    assert 'id="overview-calendar"' in page
    assert 'id="overview-radar"' in page
    assert 'id="last-sync"' in page
    assert 'aria-live="polite"' in page
    assert 'aria-label="Обновить данные"' in page
    assert 'id="coding-add"' in page
    assert 'id="coding-dialog"' in page
    assert 'id="quick-list-field"' not in page
    assert 'id="quick-priority-field"' not in page
    assert 'id="quick-project-field"' in page
    assert "history.replaceState" in script
    assert "aria-current" in script


def test_dashboard_tasks_and_code_desk_keep_mobile_actions_clear_and_compact() -> None:
    app_client, _ = client()

    page = app_client.get("/").text
    script = (Path(__file__).parents[1] / "hermes" / "native_tools" / "dashboard_assets" / "dashboard.js").read_text()
    stylesheet = (Path(__file__).parents[1] / "hermes" / "native_tools" / "dashboard_assets" / "dashboard.css").read_text()

    assert 'id="task-search"' in page
    assert 'id="task-list-filter"' in page
    assert 'id="task-menu-dialog"' in page
    assert "не коммитит и не деплоит" in page
    assert "function taskMatchesQuery" in script
    assert "function openTaskMenu" in script
    assert ".task-menu-button" in stylesheet
    assert "-webkit-line-clamp: 3" in stylesheet


def test_dashboard_styles_keep_hidden_loading_panel_out_of_the_layout() -> None:
    stylesheet = (Path(__file__).parents[1] / "hermes" / "native_tools" / "dashboard_assets" / "dashboard.css").read_text()

    assert "[hidden] { display:none !important; }" in stylesheet


def test_menu_button_requires_https_url_before_any_telegram_request() -> None:
    try:
        configure_menu_button(url="http://localhost:8788", token="not-used", chat_id=OWNER_ID)
    except ValueError as error:
        assert "HTTPS" in str(error)
    else:  # pragma: no cover - protects against a security regression.
        raise AssertionError("insecure dashboard URL was accepted")


def test_dashboard_runner_resolves_native_tools_when_executed_as_a_script() -> None:
    runner_path = Path(__file__).parents[1] / "hermes" / "scripts" / "run_dashboard.py"
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import runpy; module = runpy.run_path(__import__('sys').argv[1]); print(module['PROFILE_ROOT'])",
            str(runner_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.strip() == str(runner_path.parents[1])
