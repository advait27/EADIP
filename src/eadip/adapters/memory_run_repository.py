"""In-memory RunRepository for Phase 1.

Replaced by a PostgreSQL-backed adapter (with RLS) in Phase 2/3.
"""

from __future__ import annotations

from uuid import UUID

from eadip.domain.entities import Run


class InMemoryRunRepository:
    def __init__(self) -> None:
        self._runs: dict[UUID, Run] = {}

    async def add(self, run: Run) -> None:
        self._runs[run.id] = run

    async def get(self, run_id: UUID) -> Run | None:
        return self._runs.get(run_id)
