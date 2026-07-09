from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


logger = logging.getLogger(__name__)

ASSISTANT_LOG_HEADERS = [
    "created_at",
    "kind",
    "record_id",
    "user_id",
    "text",
]


@dataclass(frozen=True)
class GoogleServiceAccountConfig:
    spreadsheet_id: str
    sheet_name: str
    project_id: str
    private_key_id: str
    private_key: str
    client_email: str
    client_id: str
    client_x509_cert_url: str

    @property
    def is_complete(self) -> bool:
        return all(
            [
                self.spreadsheet_id,
                self.project_id,
                self.private_key_id,
                self.private_key,
                self.client_email,
                self.client_id,
                self.client_x509_cert_url,
            ]
        )


class GoogleSheetsSync:
    def __init__(self, config: GoogleServiceAccountConfig) -> None:
        if not config.is_complete:
            raise RuntimeError("Google Sheets service account config is incomplete")
        gspread, credentials_cls = _import_google_modules()
        self._config = config
        self._gspread = gspread
        self._credentials_cls = credentials_cls
        self._client = self._build_client()
        spreadsheet = self._client.open_by_key(config.spreadsheet_id)
        self._table = _WorksheetTable(
            spreadsheet=spreadsheet,
            sheet_name=config.sheet_name,
            headers=ASSISTANT_LOG_HEADERS,
            key_column=3,
        )

    def append(
        self,
        *,
        kind: str,
        user_id: int,
        text: str,
        created_at: datetime | None = None,
        record_id: str | None = None,
    ) -> bool:
        created = created_at or datetime.now(timezone.utc)
        key = _record_key(kind=kind, user_id=user_id, text=text, created_at=created, record_id=record_id)
        row = [
            created.isoformat(),
            kind,
            key,
            user_id,
            text,
        ]
        try:
            self._table.upsert_by_key(key, row)
        except Exception as exc:
            logger.warning("Google Sheets sync failed: %s", exc)
            return False
        return True

    def _build_client(self):
        scope = [
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive",
        ]
        creds_dict = {
            "type": "service_account",
            "project_id": self._config.project_id,
            "private_key_id": self._config.private_key_id,
            "private_key": self._config.private_key.replace("\\n", "\n"),
            "client_email": self._config.client_email,
            "client_id": self._config.client_id,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "client_x509_cert_url": self._config.client_x509_cert_url,
            "universe_domain": "googleapis.com",
        }
        creds = self._credentials_cls.from_json_keyfile_dict(creds_dict, scope)
        return self._gspread.authorize(creds)


class _WorksheetTable:
    def __init__(self, *, spreadsheet, sheet_name: str, headers: list[str], key_column: int) -> None:
        self._spreadsheet = spreadsheet
        self._sheet_name = sheet_name
        self._headers = headers
        self._key_column = key_column
        self._worksheet = self._ensure_worksheet()

    def upsert_by_key(self, key: str, row_values: list[Any]) -> int:
        if not key.strip():
            raise ValueError("Cannot sync a row without a non-empty key")
        row_number = self._find_row_number_by_key(key)
        end_col = _column_letter(len(self._headers))
        if row_number is None:
            row_number = self._first_empty_row_number()
            if row_number is None:
                self._worksheet.append_row(row_values, value_input_option="RAW")
                row_number = self._find_row_number_by_key(key)
            else:
                self._worksheet.update(
                    values=[row_values],
                    range_name=f"A{row_number}:{end_col}{row_number}",
                    value_input_option="RAW",
                )
        else:
            self._worksheet.update(
                values=[row_values],
                range_name=f"A{row_number}:{end_col}{row_number}",
                value_input_option="RAW",
            )
        if row_number is None:
            raise RuntimeError(f"Failed to resolve synced row for key={key}")
        return row_number

    def _ensure_worksheet(self):
        try:
            worksheet = self._spreadsheet.worksheet(self._sheet_name)
        except Exception:
            worksheet = self._spreadsheet.add_worksheet(
                title=self._sheet_name,
                rows=2000,
                cols=len(self._headers),
            )

        end_col = _column_letter(len(self._headers))
        if worksheet.row_values(1) != self._headers:
            worksheet.update(values=[self._headers], range_name=f"A1:{end_col}1", value_input_option="RAW")
            worksheet.freeze(rows=1)
        if worksheet.col_count > len(self._headers):
            worksheet.delete_columns(len(self._headers) + 1, worksheet.col_count)
        elif worksheet.col_count < len(self._headers):
            worksheet.resize(cols=len(self._headers))
        return worksheet

    def _find_row_number_by_key(self, key: str) -> int | None:
        values = self._worksheet.col_values(self._key_column)
        for index, value in enumerate(values[1:], start=2):
            if _normalize_sheet_scalar(value) == key:
                return index
        return None

    def _first_empty_row_number(self) -> int | None:
        rows = self._worksheet.get_all_values()
        for index, row in enumerate(rows[1:], start=2):
            if not any(cell.strip() for cell in row):
                return index
        return None


def _record_key(
    *,
    kind: str,
    user_id: int,
    text: str,
    created_at: datetime,
    record_id: str | None,
) -> str:
    if record_id:
        return f"{kind}:{record_id}"
    digest = hashlib.sha256(f"{user_id}:{kind}:{created_at.isoformat()}:{text}".encode("utf-8")).hexdigest()[:16]
    return f"{kind}:auto:{digest}"


def _column_letter(index: int) -> str:
    letters = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def _normalize_sheet_scalar(value: str) -> str:
    normalized = (value or "").strip()
    if not normalized:
        return ""
    if len(normalized) >= 2 and normalized.startswith("'") and normalized.endswith("'"):
        normalized = normalized[1:-1].strip()
    if normalized.startswith("'"):
        normalized = normalized[1:].strip()
    if len(normalized) >= 2 and normalized.startswith('"') and normalized.endswith('"'):
        normalized = normalized[1:-1].strip()
    return normalized


def _import_google_modules():
    try:
        import gspread
        from oauth2client.service_account import ServiceAccountCredentials
    except ImportError as exc:
        raise RuntimeError("Google Sheets dependencies are not installed. Run: .venv/bin/pip install -e '.[google]'") from exc
    return gspread, ServiceAccountCredentials
