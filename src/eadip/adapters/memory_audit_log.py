"""In-memory audit log for the no-database dev/test path.

Production uses PostgresAuditLog (append-only, hash-chained). This keeps the
same observable behavior (events accumulate, none are mutated, each tenant's
events form a hash chain) so the gateway is governed even without a database.
"""

from __future__ import annotations

from uuid import UUID

from eadip.security.audit import (
    GENESIS_HASH,
    AuditEvent,
    ChainedAuditRecord,
    ChainReport,
    chain,
    verify_chain,
)


class InMemoryAuditLog:
    def __init__(self) -> None:
        self._events: list[AuditEvent] = []
        self._chains: dict[UUID, list[ChainedAuditRecord]] = {}

    async def record(self, event: AuditEvent) -> None:
        # No await between reading the head and appending: atomic under asyncio.
        records = self._chains.setdefault(event.tenant_id, [])
        prev_hash = records[-1].hash if records else GENESIS_HASH
        records.append(chain(prev_hash, event))
        self._events.append(event)

    @property
    def events(self) -> list[AuditEvent]:
        # Copy: callers cannot mutate the log (append-only semantics).
        return list(self._events)

    def records(self, tenant_id: UUID) -> list[ChainedAuditRecord]:
        return list(self._chains.get(tenant_id, []))

    def verify(self, tenant_id: UUID) -> ChainReport:
        return verify_chain(self._chains.get(tenant_id, []))
