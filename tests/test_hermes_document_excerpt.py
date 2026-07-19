from __future__ import annotations

from pathlib import Path

import pytest

from hermes.native_tools.mcp_api import NativeToolsAPI
from hermes.native_tools.telegram_text_export import TelegramExportError, read_document_excerpt


def _write_pdf(path: Path, text: str) -> None:
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject, TextStringObject

    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    content = DecodedStreamObject()
    content.set_data(f"BT /F1 12 Tf 40 720 Td ({text}) Tj ET".encode("latin-1"))
    font = DictionaryObject()
    font[NameObject("/Type")] = NameObject("/Font")
    font[NameObject("/Subtype")] = NameObject("/Type1")
    font[NameObject("/BaseFont")] = NameObject("/Helvetica")
    resources = DictionaryObject()
    resources[NameObject("/Font")] = DictionaryObject({NameObject("/F1"): font})
    page[NameObject("/Resources")] = resources
    page[NameObject("/Contents")] = writer._add_object(content)
    with path.open("wb") as handle:
        writer.write(handle)


def test_read_document_excerpt_reads_text_files(tmp_path: Path) -> None:
    document = tmp_path / "notes.txt"
    document.write_text("Привет из документа", encoding="utf-8")

    result = read_document_excerpt(document, output_dir=tmp_path)

    assert "Привет" in result.text
    assert result.truncated is False


def test_read_document_excerpt_extracts_pdf_text(tmp_path: Path) -> None:
    document = tmp_path / "report.pdf"
    _write_pdf(document, "Hello PDF")

    result = read_document_excerpt(document, output_dir=tmp_path)

    assert "Hello PDF" in result.text


def test_read_document_excerpt_rejects_outside_and_strange_files(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.pdf"
    outside.write_bytes(b"%PDF-1.4")
    with pytest.raises(TelegramExportError, match="папке экспортов"):
        read_document_excerpt(outside, output_dir=tmp_path)

    binary = tmp_path / "archive.zip"
    binary.write_bytes(b"PK\x03\x04")
    with pytest.raises(TelegramExportError, match="txt, md, jsonl и pdf"):
        read_document_excerpt(binary, output_dir=tmp_path)


def test_native_api_file_read_excerpt_round_trip(tmp_path: Path) -> None:
    document = tmp_path / "doc.md"
    document.write_text("# Отчёт\nТекст отчёта", encoding="utf-8")
    api = NativeToolsAPI(database_path=tmp_path / "personal.sqlite3")

    import hermes.native_tools.api_telegram as api_telegram

    original = api_telegram.read_document_excerpt
    api_telegram.read_document_excerpt = lambda path, *, output_dir=None, max_chars=120_000: original(
        path, output_dir=tmp_path, max_chars=max_chars
    )
    try:
        result = api.telegram_file_read_excerpt(path=str(document))
    finally:
        api_telegram.read_document_excerpt = original

    assert "Отчёт" in result["text"]
