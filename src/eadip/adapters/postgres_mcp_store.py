"""Postgres-backed MCP server registry with RLS (Phase 8).

Persists the serialised `RegisteredServer` in `mcp_server`, scoped to the tenant.
`upsert` is keyed on (tenant, name); reads run inside the tenant's RLS context so
one tenant can never see or invoke another's connectors (SEC-09, AP-5). Mirrors
the Phase 6 PostgresCheckpointer pattern.
"""

from __future__ import annotations

from uuid import UUID

from eadip.adapters.postgres import Database
from eadip.mcp.models import RegisteredServer

_UPSERT = (
    "INSERT INTO mcp_server (id, tenant_id, name, state, server, updated_at) "
    "VALUES ($1, $2, $3, $4, $5::jsonb, now()) "
    "ON CONFLICT (tenant_id, name) DO UPDATE SET "
    "state = EXCLUDED.state, server = EXCLUDED.server, updated_at = now()"
)
_SELECT_ONE = "SELECT server FROM mcp_server WHERE name = $1"
_SELECT_ALL = "SELECT server FROM mcp_server ORDER BY name"


class PostgresServerStore:
    def __init__(self, database: Database) -> None:
        self._db = database

    async def upsert(self, server: RegisteredServer) -> None:
        async with self._db.tenant_connection(server.tenant_id) as conn:
            await conn.execute(
                _UPSERT,
                server.id,
                server.tenant_id,
                server.config.name,
                str(server.health.state),
                server.model_dump_json(),
            )

    async def get(self, tenant_id: UUID, name: str) -> RegisteredServer | None:
        async with self._db.tenant_connection(tenant_id) as conn:
            row = await conn.fetchrow(_SELECT_ONE, name)
        if row is None:
            return None
        return RegisteredServer.model_validate_json(row["server"])

    async def list(self, tenant_id: UUID) -> list[RegisteredServer]:
        async with self._db.tenant_connection(tenant_id) as conn:
            rows = await conn.fetch(_SELECT_ALL)
        return [RegisteredServer.model_validate_json(r["server"]) for r in rows]
