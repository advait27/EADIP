"""Secret/PII redaction for logs and audit detail (SEC-05, NFR-08).

Defensive scrub applied before anything sensitive could reach a log line or an
audit record. Not a substitute for never passing secrets in the first place.
"""

from __future__ import annotations

from typing import Any

_SENSITIVE_HINTS = (
    "password",
    "secret",
    "token",
    "authorization",
    "api_key",
    "apikey",
    "credential",
    "dsn",
    "private_key",
)

REDACTED = "***"


def _is_sensitive(key: str) -> bool:
    k = key.lower()
    return any(hint in k for hint in _SENSITIVE_HINTS)


def redact(data: dict[str, Any]) -> dict[str, Any]:
    """Return a shallow-redacted copy: sensitive keys masked, nested dicts walked."""
    out: dict[str, Any] = {}
    for key, value in data.items():
        if _is_sensitive(key):
            out[key] = REDACTED
        elif isinstance(value, dict):
            out[key] = redact(value)
        else:
            out[key] = value
    return out
