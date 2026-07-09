from __future__ import annotations

from uuid import uuid4

from eadip.adapters.memory_audit_log import InMemoryAuditLog
from eadip.security.audit import make_event
from eadip.security.redaction import REDACTED, redact


def test_redact_masks_sensitive_keys_and_walks_nested() -> None:
    out = redact(
        {
            "password": "p",
            "api_key": "k",
            "note": "ok",
            "nested": {"token": "t", "count": 1},
        }
    )
    assert out["password"] == REDACTED
    assert out["api_key"] == REDACTED
    assert out["note"] == "ok"
    assert out["nested"]["token"] == REDACTED
    assert out["nested"]["count"] == 1


def test_make_event_redacts_detail() -> None:
    event = make_event(
        tenant_id=uuid4(),
        actor="user",
        action="x",
        detail={"secret": "s", "ok": 1},
    )
    assert event.detail["secret"] == REDACTED
    assert event.detail["ok"] == 1


async def test_inmemory_audit_is_append_only() -> None:
    log = InMemoryAuditLog()
    await log.record(make_event(tenant_id=uuid4(), actor="u", action="a"))
    snapshot = log.events
    snapshot.clear()  # mutating the returned copy must not affect the log
    assert len(log.events) == 1
