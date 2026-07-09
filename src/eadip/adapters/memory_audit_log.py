"""In-memory audit log for the no-database dev/test path.

Production uses PostgresAuditLog (append-only). This keeps the same observable
behavior (events accumulate, none are mutated) so the gateway is governed even
without a database.
"""

from __future__ import annotations

from eadip.security.audit import AuditEvent


class InMemoryAuditLog:
    def __init__(self) -> None:
        self._events: list[AuditEvent] = []

    async def record(self, event: AuditEvent) -> None:
        self._events.append(event)

    @property
    def events(self) -> list[AuditEvent]:
        # Copy: callers cannot mutate the log (append-only semantics).
        return list(self._events)
