"""Office-format parsing (needs the `data` extra; skipped otherwise)."""

from __future__ import annotations

import io

import pytest

from eadip.ingestion.parsing import parse

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def test_docx_roundtrip() -> None:
    docx = pytest.importorskip("docx")
    buf = io.BytesIO()
    document = docx.Document()
    document.add_paragraph("Hello from docx")
    document.save(buf)
    assert "Hello from docx" in parse(buf.getvalue(), DOCX).text


def test_xlsx_roundtrip() -> None:
    openpyxl = pytest.importorskip("openpyxl")
    buf = io.BytesIO()
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet["A1"] = "Revenue"
    sheet["B1"] = 42
    workbook.save(buf)
    text = parse(buf.getvalue(), XLSX).text
    assert "Revenue" in text
    assert "42" in text
