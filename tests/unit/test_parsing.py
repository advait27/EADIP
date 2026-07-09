from __future__ import annotations

import pytest

from eadip.ingestion.parsing import UnsupportedContentType, guess_content_type, parse


def test_plain_text() -> None:
    doc = parse(b"hello world", "text/plain")
    assert doc.text == "hello world"


def test_html_strips_tags_and_scripts() -> None:
    html = b"<html><head><style>x{}</style></head><body><h1>Title</h1>"
    html += b"<script>evil()</script><p>Body text</p></body></html>"
    doc = parse(html, "text/html")
    assert "Title" in doc.text
    assert "Body text" in doc.text
    assert "evil" not in doc.text
    assert "<" not in doc.text


def test_csv_to_text() -> None:
    doc = parse(b"a,b\n1,2\n", "text/csv")
    assert "a b" in doc.text
    assert "1 2" in doc.text


def test_content_type_with_charset_param() -> None:
    assert parse(b"hi", "text/plain; charset=utf-8").text == "hi"


def test_octet_stream_falls_back_to_filename() -> None:
    doc = parse(b"# Heading", "application/octet-stream", filename="notes.md")
    assert doc.content_type == "text/markdown"


def test_unsupported_type_raises() -> None:
    with pytest.raises(UnsupportedContentType):
        parse(b"...", "application/x-unknown")


def test_guess_content_type() -> None:
    assert guess_content_type("a.html") == "text/html"
    assert guess_content_type("a.pdf") == "application/pdf"
    assert guess_content_type("a.unknown") == "application/octet-stream"
