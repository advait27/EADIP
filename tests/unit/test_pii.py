from __future__ import annotations

from eadip.ingestion.pii import PiiRedactor


def test_redacts_email_and_reports_type() -> None:
    text, found = PiiRedactor().redact("contact priya@example.com for details")
    assert "priya@example.com" not in text
    assert "[REDACTED_EMAIL]" in text
    assert "email" in found


def test_redacts_multiple_pii_types() -> None:
    text, found = PiiRedactor().redact(
        "SSN 123-45-6789, call 415-555-0188, ip 10.0.0.1, mail a@b.co"
    )
    assert "123-45-6789" not in text
    assert "10.0.0.1" not in text
    assert {"ssn", "phone", "ip", "email"} <= set(found)


def test_clean_text_unchanged() -> None:
    text, found = PiiRedactor().redact("Revenue fell 12% in EMEA last quarter.")
    assert found == ()
    assert text == "Revenue fell 12% in EMEA last quarter."
