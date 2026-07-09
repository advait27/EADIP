"""Content PII detection + redaction for stored artifacts (NFR-08).

Distinct from eadip.security.redaction (which masks sensitive *keys* in logs and
audit detail); this scrubs PII *values* out of document text before it is chunked
and stored in the vector store.
"""

from __future__ import annotations

import re

# (type, compiled pattern, placeholder). Order matters: more specific first.
_PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    ("email", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"), "[REDACTED_EMAIL]"),
    ("credit_card", re.compile(r"\b(?:\d[ -]?){13,16}\b"), "[REDACTED_CC]"),
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[REDACTED_SSN]"),
    (
        "phone",
        re.compile(r"\b(?:\+?\d{1,3}[ .-]?)?(?:\(?\d{3}\)?[ .-]?)\d{3}[ .-]?\d{4}\b"),
        "[REDACTED_PHONE]",
    ),
    ("ip", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "[REDACTED_IP]"),
]


class PiiRedactor:
    def redact(self, text: str) -> tuple[str, tuple[str, ...]]:
        """Return (redacted_text, sorted unique PII types found)."""
        found: set[str] = set()
        out = text
        for pii_type, pattern, placeholder in _PATTERNS:
            if pattern.search(out):
                found.add(pii_type)
                out = pattern.sub(placeholder, out)
        return out, tuple(sorted(found))
