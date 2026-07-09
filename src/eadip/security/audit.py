"""Append-only audit trail (FR-051, SEC-08).

Records authentication, authorization decisions, queries, tool calls, and
approvals. The append-only guarantee is enforced at the storage layer (Postgres
trigger + least-privilege role); this module defines the event and the port.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID

from eadip.security.redaction import redact


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class AuditEvent:
    tenant_id: UUID
    actor: str  # user id, or "system:<agent>"
    action: str  # e.g. "authz.decision", "run.create", "sql.execute"
    detail: dict[str, Any] = field(default_factory=dict)
    run_id: UUID | None = None
    at: datetime = field(default_factory=_utcnow)


def make_event(
    *,
    tenant_id: UUID,
    actor: str,
    action: str,
    detail: dict[str, Any] | None = None,
    run_id: UUID | None = None,
) -> AuditEvent:
    """Build an audit event, redacting any sensitive fields in `detail`."""
    return AuditEvent(
        tenant_id=tenant_id,
        actor=actor,
        action=action,
        detail=redact(detail or {}),
        run_id=run_id,
    )


class AuditLog(Protocol):
    async def record(self, event: AuditEvent) -> None: ...
