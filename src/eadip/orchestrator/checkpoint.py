"""Checkpointer (TAD Ch 6, AP-7): durable RunState so a run resumes after a
crash/restart. In-memory default; the Postgres adapter (RLS-scoped) is the
production store. Save is called after every node, so resume picks up at the
last completed step.
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from eadip.orchestrator.state import RunState


class Checkpointer(Protocol):
    async def save(self, state: RunState) -> None: ...

    async def load(self, tenant_id: UUID, run_id: UUID) -> RunState | None: ...


class InMemoryCheckpointer:
    """Process-local checkpoint store. Tenant-checked on load to mirror the RLS
    isolation the Postgres adapter enforces."""

    def __init__(self) -> None:
        self._store: dict[UUID, str] = {}

    async def save(self, state: RunState) -> None:
        self._store[state.run_id] = state.model_dump_json()

    async def load(self, tenant_id: UUID, run_id: UUID) -> RunState | None:
        raw = self._store.get(run_id)
        if raw is None:
            return None
        state = RunState.model_validate_json(raw)
        return state if state.tenant_id == tenant_id else None
