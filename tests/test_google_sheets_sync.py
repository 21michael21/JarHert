from __future__ import annotations

from datetime import datetime, timezone

from assistant.google_sheets_sync import GoogleServiceAccountConfig, _record_key


def test_google_service_account_config_detects_missing_fields() -> None:
    config = GoogleServiceAccountConfig(
        spreadsheet_id="sheet",
        sheet_name="AI Brooch",
        project_id="project",
        private_key_id="",
        private_key="key",
        client_email="bot@example.iam.gserviceaccount.com",
        client_id="client",
        client_x509_cert_url="https://example.com/cert",
    )

    assert not config.is_complete


def test_google_sheet_record_key_prefers_record_id() -> None:
    key = _record_key(
        kind="idea",
        user_id=1,
        text="текст",
        created_at=datetime(2026, 7, 9, tzinfo=timezone.utc),
        record_id="42",
    )

    assert key == "idea:42"


def test_google_sheet_record_key_is_stable_without_record_id() -> None:
    created_at = datetime(2026, 7, 9, tzinfo=timezone.utc)

    first = _record_key(kind="idea", user_id=1, text="текст", created_at=created_at, record_id=None)
    second = _record_key(kind="idea", user_id=1, text="текст", created_at=created_at, record_id=None)

    assert first == second
    assert first.startswith("idea:auto:")
