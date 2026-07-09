"""Postgres MCP registry store: RegisteredServer round-trips and is tenant-
isolated by RLS (Phase 8). Skipped when no database is reachable; CI runs it."""

from __future__ import annotations

import os
from uuid import UUID, uuid4

import pytest
import pytest_asyncio

from eadip.adapters.migrations import apply_migrations
from eadip.adapters.postgres import Database
from eadip.adapters.postgres_mcp_store import PostgresServerStore
from eadip.mcp.models import (
    RegisteredServer,
    ServerHealth,
    ServerState,
    ToolPermission,
    ToolServerConfig,
    ToolSpec,
)
from eadip.security.rbac import Effect

DSN = os.environ.get("EADIP_TEST_POSTGRES_DSN", "postgresql://eadip:eadip@localhost:5432/eadip")


@pytest_asyncio.fixture
async def db() -> Database:
    database = Database(DSN)
    try:
        await database.connect()
        await database.ping()
    except Exception:
        pytest.skip("Postgres not available for integration tests")
    await apply_migrations(database)
    async with database.connection() as conn:
        await conn.execute("TRUNCATE mcp_server, tenant RESTART IDENTITY CASCADE")
    yield database
    await database.close()


async def _tenant(database: Database) -> UUID:
    tid = uuid4()
    async with database.connection() as conn:
        await conn.execute("INSERT INTO tenant (id, name) VALUES ($1, $2)", tid, "t")
    return tid


def _server(tenant_id: UUID) -> RegisteredServer:
    return RegisteredServer(
        tenant_id=tenant_id,
        config=ToolServerConfig(name="crm", transport="inprocess"),
        tools=[
            ToolSpec(
                name="fx_rate",
                description="rate lookup",
                input_schema={"type": "object", "properties": {"c": {"type": "string"}}},
                permission=ToolPermission(effect=Effect.READ, domains=("metrics",)),
            )
        ],
        health=ServerHealth(state=ServerState.HEALTHY),
    )


async def test_server_round_trips(db: Database) -> None:
    tenant = await _tenant(db)
    store = PostgresServerStore(db)
    await store.upsert(_server(tenant))

    loaded = await store.get(tenant, "crm")
    assert loaded is not None
    assert loaded.config.transport == "inprocess"
    assert loaded.tool("fx_rate") is not None
    assert loaded.tool("fx_rate").permission.effect == Effect.READ


async def test_upsert_and_tenant_isolation(db: Database) -> None:
    ta = await _tenant(db)
    tb = await _tenant(db)
    store = PostgresServerStore(db)

    server = _server(ta)
    await store.upsert(server)
    server.health = ServerHealth(state=ServerState.DEGRADED)
    await store.upsert(server)  # upsert on (tenant, name)
    reloaded = await store.get(ta, "crm")
    assert reloaded is not None and reloaded.health.state is ServerState.DEGRADED

    # Tenant B sees none of tenant A's servers (RLS).
    assert await store.get(tb, "crm") is None
    assert await store.list(tb) == []
