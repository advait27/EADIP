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

import sqlglot
from sqlglot import exp

from eadip.adapters.demo_finance import FINANCE_SCHEMA, demo_finance_rows
from eadip.ingestion.datasets import (
    DatasetEntry,
    DatasetRegistry,
    InMemoryDatasetRegistry,
    ParsedDataset,
    physical_name,
)
from eadip.ports.warehouse import QueryResult, Warehouse, WarehouseError, WarehouseSchema

_CREATE = (
    "CREATE TABLE IF NOT EXISTS finance_metrics ("
    " tenant_id VARCHAR, region VARCHAR, product_line VARCHAR, period VARCHAR,"
    " revenue DOUBLE, cogs DOUBLE, opex DOUBLE)"
)


_SQL_TYPES = {"number": "DOUBLE", "text": "VARCHAR", "date": "DATE"}


class DuckDBWarehouse(Warehouse):
    def __init__(
        self, db_path: str | None = None, *, registry: DatasetRegistry | None = None
    ) -> None:
        # A file (not :memory:) so the dataset survives the connection and one
        # file per process (the gateway caches the instance).
        self._path = db_path or str(Path(tempfile.mkdtemp(prefix="eadip-wh-")) / "warehouse.duckdb")
        self._seeded: set[str] = set()
        self._seed_lock = asyncio.Lock()  # serialize writes only, never reads
        self._con: Any = None
        self._con_guard = threading.Lock()
        # Uploaded datasets (Glass Box): logical name -> physical table, per tenant.
        self.datasets: DatasetRegistry = registry or InMemoryDatasetRegistry()

    async def schema(self, tenant_id: UUID) -> WarehouseSchema:
        extra = tuple(e.schema for e in self.datasets.entries(tenant_id))
        if not extra:
            return FINANCE_SCHEMA
        return WarehouseSchema(
            tables=(*FINANCE_SCHEMA.tables, *extra), tenant_column=FINANCE_SCHEMA.tenant_column
        )

    async def execute(
        self, tenant_id: UUID, sql: str, *, row_cap: int, timeout_s: float
    ) -> QueryResult:
        if str(tenant_id) not in self._seeded:
            async with self._seed_lock:
                await asyncio.to_thread(self._ensure_seeded, tenant_id)
        return await asyncio.to_thread(self._run_query, self._physical_sql(tenant_id, sql), row_cap)

    # --- datasets (Glass Box) -------------------------------------------------
    async def register_dataset(
        self, tenant_id: UUID, name: str, parsed: ParsedDataset
    ) -> DatasetEntry:
        """Create (or replace) the tenant's table for an uploaded dataset."""
        entry = DatasetEntry(
            tenant_id=tenant_id,
            name=name.lower(),
            physical=physical_name(tenant_id, name.lower()),
            schema=parsed.table_schema(name.lower()),
            row_count=parsed.row_count,
        )
        async with self._seed_lock:
            await asyncio.to_thread(self._create_dataset, entry, parsed)
        self.datasets.register(entry)
        return entry

    async def drop_dataset(self, tenant_id: UUID, name: str) -> bool:
        entry = self.datasets.remove(tenant_id, name)
        if entry is None:
            return False
        async with self._seed_lock:
            await asyncio.to_thread(self._drop_physical, entry.physical)
        return True

    def _physical_sql(self, tenant_id: UUID, sql: str) -> str:
        """Rewrite this tenant's logical dataset names to their physical tables.
        Provenance SQL stays logical (and readable) everywhere else."""
        entries = {e.name: e.physical for e in self.datasets.entries(tenant_id)}
        if not entries:
            return sql
        tree = sqlglot.parse_one(sql, read="duckdb")
        touched = False
        for table in tree.find_all(exp.Table):
            physical = entries.get(table.name.lower())
            if physical is not None:
                table.set("this", exp.to_identifier(physical))
                touched = True
        return tree.sql(dialect="duckdb") if touched else sql

    def _create_dataset(self, entry: DatasetEntry, parsed: ParsedDataset) -> None:
        cur = self._connection().cursor()
        try:
            cols = ", ".join(
                f'"{c.name}" {_SQL_TYPES.get(c.type, "VARCHAR")}' for c in entry.schema.columns
            )
            cur.execute(f'DROP TABLE IF EXISTS "{entry.physical}"')
            cur.execute(f'CREATE TABLE "{entry.physical}" ({cols})')  # nosec B608 - identifiers validated
            placeholders = ", ".join("?" for _ in entry.schema.columns)
            cur.executemany(
                f'INSERT INTO "{entry.physical}" VALUES ({placeholders})',  # nosec B608
                [[str(entry.tenant_id), *row] for row in parsed.rows],
            )
        finally:
            cur.close()

    def _drop_physical(self, physical: str) -> None:
        cur = self._connection().cursor()
        try:
            cur.execute(f'DROP TABLE IF EXISTS "{physical}"')  # nosec B608 - our own identifier
        finally:
            cur.close()

    # --- blocking helpers (run via asyncio.to_thread) -------------------------
    def _connection(self) -> Any:
        """The one shared connection; per-call cursors give MVCC concurrency."""
        with self._con_guard:
            if self._con is None:
                import duckdb

                self._con = duckdb.connect(self._path)
                self._con.execute(_CREATE)
                # Orphaned dataset tables (registry is process memory; the file
                # may outlive it) are unreachable AND dropped, so nothing lingers.
                rows = self._con.execute(
                    "SELECT table_name FROM information_schema.tables WHERE table_name LIKE 'ds_%'"
                ).fetchall()
                for (name,) in rows:
                    self._con.execute(f'DROP TABLE IF EXISTS "{name}"')  # nosec B608
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
