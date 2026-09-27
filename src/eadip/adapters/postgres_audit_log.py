"""PostgreSQL-backed append-only, hash-chained audit log (FR-051, SEC-08).

Inserts under the event's tenant RLS context. Mutation is blocked by the
append-only trigger from migration 0002; each row is linked into the tenant's
hash chain (migration 0009) so edits that bypass the trigger are detectable by
`verify` — unless the chain is recomputed too (see eadip.security.audit).
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any
from uuid import UUID

from eadip.adapters.postgres import Database
from eadip.security.audit import (
    GENESIS_HASH,
    AuditEvent,
    ChainedAuditRecord,
    ChainReport,
    event_hash,
    verify_chain,
)

# Serializes appends per tenant (two-key form namespaces the lock), so the head
# read below and the insert are atomic w.r.t. other writers of the same tenant.
# Transaction-scoped: released at commit, after the new row is visible.
_LOCK = "SELECT pg_advisory_xact_lock(hashtext('eadip.audit_event'), hashtext($1::text))"
_HEAD = (
    "SELECT hash FROM audit_event WHERE tenant_id = $1 AND hash IS NOT NULL "
    "ORDER BY id DESC LIMIT 1"
)
_INSERT = (
    "INSERT INTO audit_event (tenant_id, run_id, actor, action, detail, at, prev_hash, hash) "
    "VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, $8)"
)
# detail as text so numbers are parsed exactly (Decimal), not via float.
_SELECT_CHAIN = (
    "SELECT id, tenant_id, run_id, actor, action, detail::text AS detail, at, prev_hash, hash "
    "FROM audit_event WHERE tenant_id = $1 ORDER BY id"
)


def _row_event(row: Any) -> AuditEvent:
    return AuditEvent(
        tenant_id=row["tenant_id"],
        actor=row["actor"],
        action=row["action"],
        detail=json.loads(row["detail"], parse_float=Decimal),
        run_id=row["run_id"],
        at=row["at"],
    )


class PostgresAuditLog:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def record(self, event: AuditEvent) -> None:
        async with self._db.tenant_connection(event.tenant_id) as conn:
            await conn.execute(_LOCK, str(event.tenant_id))
            prev_hash = await conn.fetchval(_HEAD, event.tenant_id) or GENESIS_HASH
            # Hash the exact values inserted: `at` is microsecond-precise in both
            # Python and timestamptz; detail is canonicalized so the JSONB
            # round trip (key order, number spelling) does not change the hash.
            await conn.execute(
                _INSERT,
                event.tenant_id,
                event.run_id,
                event.actor,
                event.action,
                json.dumps(event.detail),
                event.at,
                prev_hash,
                event_hash(prev_hash, event),
            )

    async def verify(self, tenant_id: UUID) -> ChainReport:
        """Verify the tenant's chained rows in id order.

        Leading rows with NULL hashes predate migration 0009 and are skipped. A
        NULL-hash row after the chain has started cannot come from `record`,
        so it is reported as a break (a row un-chained to hide it).
        """
        async with self._db.tenant_connection(tenant_id) as conn:
            rows = await conn.fetch(_SELECT_CHAIN, tenant_id)
        records: list[ChainedAuditRecord] = []
        for row in rows:
            if row["hash"] is None:
                if records:
                    report = verify_chain(records)
                    if not report.ok:
                        return report
                    return ChainReport(
                        ok=False,
                        checked=len(records),
                        anchored=report.anchored,
                        anchor_prev_hash=report.anchor_prev_hash,
                        head_hash=report.head_hash,
                        break_index=len(records),
                        reason=f"unchained row id={row['id']} after the chain started",
                    )
                continue
            records.append(
                ChainedAuditRecord(
                    event=_row_event(row), prev_hash=row["prev_hash"], hash=row["hash"]
                )
            )
        return verify_chain(records)
