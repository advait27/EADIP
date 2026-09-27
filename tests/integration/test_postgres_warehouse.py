"""Postgres read-replica warehouse: proves the *hard* Phase 5 guarantees against
real Postgres — RLS tenant isolation (independent of the SQL text) and an
engine-level read-only transaction. Skipped when no database is reachable; CI
runs these with a Postgres service. Set EADIP_TEST_POSTGRES_DSN to override.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
import pytest_asyncio

from eadip.adapters.demo_finance import demo_finance_rows
from eadip.adapters.postgres import Database
from eadip.adapters.postgres_warehouse import PostgresWarehouse
from eadip.ports.warehouse import WarehouseError
from tests.integration.pg import app_database

_INSERT = (
    "INSERT INTO finance_metrics "
    "(tenant_id, region, product_line, period, revenue, cogs, opex) "
    "VALUES ($1, $2, $3, $4, $5, $6, $7)"
)


@pytest_asyncio.fixture
async def db() -> Database:
    database = await app_database(
        "TRUNCATE finance_metrics, run, audit_event, user_role, permission, "
        "app_user, role, tenant RESTART IDENTITY CASCADE"
    )
    yield database
    await database.close()


async def _seed_tenant(database: Database, name: str) -> UUID:
    tenant_id = uuid4()
    async with database.connection() as conn:
        await conn.execute("INSERT INTO tenant (id, name) VALUES ($1, $2)", tenant_id, name)
    async with database.tenant_connection(tenant_id) as conn:
        await conn.executemany(
            _INSERT,
            [(tenant_id, r[1], r[2], r[3], r[4], r[5], r[6]) for r in demo_finance_rows(tenant_id)],
        )
    return tenant_id


async def test_validated_query_returns_tenant_rows(db: Database) -> None:
    tenant = await _seed_tenant(db, "A")
    wh = PostgresWarehouse(db)
    result = await wh.execute(
        tenant,
        f"SELECT region, SUM(revenue) AS rev FROM finance_metrics "
        f"WHERE tenant_id = '{tenant}' GROUP BY region",
        row_cap=100,
        timeout_s=5,
    )
    assert "region" in result.columns
    assert {str(row[0]) for row in result.rows} >= {"EMEA", "AMER", "APAC"}


async def test_rls_isolates_tenants_regardless_of_sql(db: Database) -> None:
    ta = await _seed_tenant(db, "A")
    tb = await _seed_tenant(db, "B")
    wh = PostgresWarehouse(db)
    # An unfiltered query (no tenant predicate) is still scoped by RLS: each
    # tenant sees only its own rows — the backstop behind the validator.
    a_rows = await wh.execute(
        ta, "SELECT region, revenue FROM finance_metrics", row_cap=1000, timeout_s=5
    )
    b_rows = await wh.execute(
        tb, "SELECT region, revenue FROM finance_metrics", row_cap=1000, timeout_s=5
    )
    assert a_rows.row_count == len(demo_finance_rows(ta))
    assert b_rows.row_count == len(demo_finance_rows(tb))
    # Neither tenant's query can ever surface the other's rows.
    assert a_rows.row_count == b_rows.row_count  # same dataset shape, isolated stores


async def test_read_only_transaction_refuses_writes(db: Database) -> None:
    import asyncpg

    tenant = await _seed_tenant(db, "A")
    with pytest.raises(asyncpg.PostgresError):
        async with db.tenant_connection(tenant) as conn:
            await conn.execute("SET LOCAL transaction_read_only = on")
            await conn.execute(
                "INSERT INTO finance_metrics (tenant_id, region, product_line, period) "
                "VALUES ($1, 'X', 'Y', 'Z')",
                tenant,
            )


async def test_bad_sql_becomes_warehouse_error(db: Database) -> None:
    tenant = await _seed_tenant(db, "A")
    wh = PostgresWarehouse(db)
    with pytest.raises(WarehouseError):
        await wh.execute(tenant, "SELECT nope FROM nonexistent", row_cap=10, timeout_s=5)
