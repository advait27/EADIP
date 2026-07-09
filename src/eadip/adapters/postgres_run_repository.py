"""Tenant-scoped RunRepository backed by PostgreSQL + RLS.

Bound to one tenant at construction; every query runs inside that tenant's RLS
context, so even a lookup by id cannot read another tenant's run (SEC-09).
"""

from __future__ import annotations

from uuid import UUID

from eadip.adapters.postgres import Database
from eadip.domain.entities import Run, RunStatus

_INSERT = (
    "INSERT INTO run (id, tenant_id, user_id, question, status, cost_usd, created_at) "
    "VALUES ($1, $2, $3, $4, $5::run_status, $6, $7)"
)
_SELECT = (
    "SELECT id, tenant_id, user_id, question, status, cost_usd, created_at, finished_at "
    "FROM run WHERE id = $1"
)


class PostgresRunRepository:
    def __init__(self, db: Database, tenant_id: UUID) -> None:
        self._db = db
        self._tenant_id = tenant_id

    async def add(self, run: Run) -> None:
        async with self._db.tenant_connection(self._tenant_id) as conn:
            await conn.execute(
                _INSERT,
                run.id,
                run.tenant_id,
                run.user_id,
                run.question,
                str(run.status),
                run.cost_usd,
                run.created_at,
            )

    async def get(self, run_id: UUID) -> Run | None:
        async with self._db.tenant_connection(self._tenant_id) as conn:
            row = await conn.fetchrow(_SELECT, run_id)
        if row is None:
            return None
        return Run(
            id=row["id"],
            tenant_id=row["tenant_id"],
            user_id=row["user_id"],
            question=row["question"],
            status=RunStatus(row["status"]),
            cost_usd=float(row["cost_usd"]),
            created_at=row["created_at"],
            finished_at=row["finished_at"],
        )
