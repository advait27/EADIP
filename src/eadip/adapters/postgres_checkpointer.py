"""Postgres-backed checkpointer with RLS (Phase 6, resume-on-crash).

Stores the serialised RunState in `run_checkpoint`, scoped to the tenant. Save
is an upsert; load reads within the tenant's RLS context, so a checkpoint can
only ever be resumed by its own tenant (SEC-09, AP-5).
"""

from __future__ import annotations

from uuid import UUID

from eadip.adapters.postgres import Database
from eadip.orchestrator.state import RunState

_UPSERT = (
    "INSERT INTO run_checkpoint (run_id, tenant_id, status, iteration, state, updated_at) "
    "VALUES ($1, $2, $3, $4, $5::jsonb, now()) "
    "ON CONFLICT (run_id) DO UPDATE SET "
    "status = EXCLUDED.status, iteration = EXCLUDED.iteration, "
    "state = EXCLUDED.state, updated_at = now()"
)
_SELECT = "SELECT state FROM run_checkpoint WHERE run_id = $1"


class PostgresCheckpointer:
    def __init__(self, database: Database) -> None:
        self._db = database

    async def save(self, state: RunState) -> None:
        async with self._db.tenant_connection(state.tenant_id) as conn:
            await conn.execute(
                _UPSERT,
                state.run_id,
                state.tenant_id,
                str(state.status),
                state.iterations,
                state.model_dump_json(),
            )

    async def load(self, tenant_id: UUID, run_id: UUID) -> RunState | None:
        async with self._db.tenant_connection(tenant_id) as conn:
            row = await conn.fetchrow(_SELECT, run_id)
        if row is None:
            return None
        return RunState.model_validate_json(row["state"])
