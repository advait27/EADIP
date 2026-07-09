"""PostgreSQL-backed append-only audit log (FR-051, SEC-08).

Inserts under the event's tenant RLS context. Mutation is blocked by the
append-only trigger from migration 0002.
"""

from __future__ import annotations

import json

from eadip.adapters.postgres import Database
from eadip.security.audit import AuditEvent

_INSERT = (
    "INSERT INTO audit_event (tenant_id, run_id, actor, action, detail, at) "
    "VALUES ($1, $2, $3, $4, $5::jsonb, $6)"
)


class PostgresAuditLog:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def record(self, event: AuditEvent) -> None:
        async with self._db.tenant_connection(event.tenant_id) as conn:
            await conn.execute(
                _INSERT,
                event.tenant_id,
                event.run_id,
                event.actor,
                event.action,
                json.dumps(event.detail),
                event.at,
            )
