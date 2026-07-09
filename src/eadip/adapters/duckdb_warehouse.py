"""In-process analytical warehouse backed by DuckDB (TAD Ch 8.2).

This is the default analytics backend: it seeds the deterministic demo finance
dataset per tenant and executes already-validated SQL. Defence in depth — every
statement arrives through the sqlglot AST safety gate (read-only SELECT only)
and is additionally wrapped as a table subquery (`SELECT * FROM (...)`), which
is a structural refusal of anything that is not a SELECT. The row cap is
enforced by that wrapper; DuckDB has no statement timeout, so that bound is
documented as a Postgres-replica-only control.

Concurrency (Phase 12, NFR-02): queries run on per-call **cursors of one shared
connection** — DuckDB's MVCC executes reads concurrently, so the interactive
analytics path has no single-node serialization point. Only the one-time
per-tenant seeding (a write) is serialized behind a lock. The Phase 11 load
gate caught the previous design (a global lock around every query) queueing
120 concurrent analytics calls to a 9s p95; this removes that bottleneck.

``duckdb`` is imported lazily (it lives in the ``data`` extra), so importing
this module — and the safety-gate unit tests — needs no engine.
"""

from __future__ import annotations

import asyncio
import tempfile
import threading
from pathlib import Path
from typing import Any
from uuid import UUID

from eadip.adapters.demo_finance import FINANCE_SCHEMA, demo_finance_rows
from eadip.ports.warehouse import QueryResult, Warehouse, WarehouseError, WarehouseSchema

_CREATE = (
    "CREATE TABLE IF NOT EXISTS finance_metrics ("
    " tenant_id VARCHAR, region VARCHAR, product_line VARCHAR, period VARCHAR,"
    " revenue DOUBLE, cogs DOUBLE, opex DOUBLE)"
)


class DuckDBWarehouse(Warehouse):
    def __init__(self, db_path: str | None = None) -> None:
        # A file (not :memory:) so the dataset survives the connection and one
        # file per process (the gateway caches the instance).
        self._path = db_path or str(Path(tempfile.mkdtemp(prefix="eadip-wh-")) / "warehouse.duckdb")
        self._seeded: set[str] = set()
        self._seed_lock = asyncio.Lock()  # serialize writes only, never reads
        self._con: Any = None
        self._con_guard = threading.Lock()

    async def schema(self, tenant_id: UUID) -> WarehouseSchema:
        return FINANCE_SCHEMA

    async def execute(
        self, tenant_id: UUID, sql: str, *, row_cap: int, timeout_s: float
    ) -> QueryResult:
        if str(tenant_id) not in self._seeded:
            async with self._seed_lock:
                await asyncio.to_thread(self._ensure_seeded, tenant_id)
        return await asyncio.to_thread(self._run_query, sql, row_cap)

    # --- blocking helpers (run via asyncio.to_thread) -------------------------
    def _connection(self) -> Any:
        """The one shared connection; per-call cursors give MVCC concurrency."""
        with self._con_guard:
            if self._con is None:
                import duckdb

                self._con = duckdb.connect(self._path)
                self._con.execute(_CREATE)
            return self._con

    def _ensure_seeded(self, tenant_id: UUID) -> None:
        key = str(tenant_id)
        if key in self._seeded:
            return
        cur = self._connection().cursor()
        try:
            exists = cur.execute(
                "SELECT 1 FROM finance_metrics WHERE tenant_id = ? LIMIT 1", [key]
            ).fetchone()
            if not exists:
                cur.executemany(
                    "INSERT INTO finance_metrics VALUES (?, ?, ?, ?, ?, ?, ?)",
                    demo_finance_rows(tenant_id),
                )
        finally:
            cur.close()
        self._seeded.add(key)

    def _run_query(self, sql: str, row_cap: int) -> QueryResult:
        cur = self._connection().cursor()
        try:
            # `sql` only ever arrives through the sqlglot AST safety gate (validated
            # read-only SELECT); row_cap is an int. The subquery wrapper is itself a
            # structural refusal of any non-SELECT statement.
            wrapped = f"SELECT * FROM (\n{sql}\n) AS _capped LIMIT {row_cap + 1}"  # nosec B608
            result = cur.execute(wrapped)
            columns = tuple(d[0] for d in result.description)
            fetched: list[tuple[Any, ...]] = [tuple(r) for r in result.fetchall()]
        except Exception as exc:  # noqa: BLE001 — engine refusal/error
            raise WarehouseError("query execution failed", {"error": str(exc)}) from exc
        finally:
            cur.close()
        truncated = len(fetched) > row_cap
        return QueryResult(columns=columns, rows=tuple(fetched[:row_cap]), truncated=truncated)
