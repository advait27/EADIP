"""Postgres read-replica warehouse (TAD Ch 8.1, "read-replica execution").

Runs already-validated SQL inside a tenant transaction that is:
  - RLS-scoped (``app.tenant_id`` set, FORCE row-level security on the table) —
    the *hard* tenant-isolation guarantee, independent of the SQL text;
  - read-only at the engine (``transaction_read_only``) — defence in depth;
  - bounded by ``statement_timeout`` and a wrapped row cap (NFR-06).
"""

from __future__ import annotations

from uuid import UUID

from eadip.adapters.demo_finance import FINANCE_SCHEMA
from eadip.adapters.postgres import Database
from eadip.ports.warehouse import QueryResult, Warehouse, WarehouseError, WarehouseSchema


class PostgresWarehouse(Warehouse):
    def __init__(self, database: Database) -> None:
        self._db = database

    async def schema(self, tenant_id: UUID) -> WarehouseSchema:
        return FINANCE_SCHEMA

    async def execute(
        self, tenant_id: UUID, sql: str, *, row_cap: int, timeout_s: float
    ) -> QueryResult:
        # `sql` only ever arrives through the sqlglot AST safety gate (validated
        # read-only SELECT); row_cap is an int. Not user-string interpolation.
        wrapped = f"SELECT * FROM (\n{sql}\n) AS _capped LIMIT {row_cap + 1}"  # nosec B608
        try:
            async with self._db.tenant_connection(tenant_id) as conn:
                await conn.execute("SET LOCAL transaction_read_only = on")
                await conn.execute(f"SET LOCAL statement_timeout = {int(timeout_s * 1000)}")
                stmt = await conn.prepare(wrapped)
                columns = tuple(a.name for a in stmt.get_attributes())
                records = await stmt.fetch()
        except Exception as exc:  # noqa: BLE001 — any engine refusal -> WarehouseError
            raise WarehouseError("query execution failed", {"error": str(exc)}) from exc
        rows = tuple(tuple(r) for r in records)
        truncated = len(rows) > row_cap
        return QueryResult(columns=columns, rows=rows[:row_cap], truncated=truncated)
