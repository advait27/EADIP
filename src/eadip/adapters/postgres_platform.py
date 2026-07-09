"""Postgres-backed platform stores (Phase 11): prompt versions + tenant budgets.

Prompt artifacts are platform-global (admin-governed configuration, not tenant
data) — no RLS; content immutability is enforced structurally by the
`prompt_version_immutable` trigger (migration 0007), so even a buggy caller
cannot rewrite a served prompt. Budgets are administered cross-tenant by the
platform admin, so they use the plain connection too.
"""

from __future__ import annotations

from uuid import UUID

from eadip.adapters.postgres import Database
from eadip.platform.models import PromptVersion, TenantBudget

_PROMPT_INSERT = (
    "INSERT INTO prompt_version (id, name, version, artifact, content_sha, stage) "
    "VALUES ($1, $2, $3, $4::jsonb, $5, $6)"
)
_PROMPT_SAVE = (
    "UPDATE prompt_version SET artifact = $3::jsonb, stage = $4, updated_at = now() "
    "WHERE name = $1 AND version = $2"
)
_PROMPT_GET = "SELECT artifact FROM prompt_version WHERE name = $1 AND version = $2"
_PROMPT_VERSIONS = "SELECT artifact FROM prompt_version WHERE name = $1 ORDER BY version"
_PROMPT_NAMES = "SELECT DISTINCT name FROM prompt_version ORDER BY name"

_RESIDENCY_UPSERT = (
    "INSERT INTO tenant_residency (tenant_id, region, updated_at) "
    "VALUES ($1, $2, now()) "
    "ON CONFLICT (tenant_id) DO UPDATE SET region = EXCLUDED.region, updated_at = now()"
)
_RESIDENCY_GET = "SELECT region FROM tenant_residency WHERE tenant_id = $1"
_RESIDENCY_ALL = "SELECT tenant_id, region FROM tenant_residency ORDER BY tenant_id"

_BUDGET_UPSERT = (
    "INSERT INTO tenant_budget (tenant_id, budget, updated_at) "
    "VALUES ($1, $2::jsonb, now()) "
    "ON CONFLICT (tenant_id) DO UPDATE SET budget = EXCLUDED.budget, updated_at = now()"
)
_BUDGET_GET = "SELECT budget FROM tenant_budget WHERE tenant_id = $1"
_BUDGET_ALL = "SELECT budget FROM tenant_budget ORDER BY tenant_id"


class PostgresPromptStore:
    def __init__(self, database: Database) -> None:
        self._db = database

    async def add(self, version: PromptVersion) -> None:
        async with self._db.connection() as conn:
            await conn.execute(
                _PROMPT_INSERT,
                version.id,
                version.name,
                version.version,
                version.model_dump_json(),
                version.content_sha,
                str(version.stage),
            )

    async def save(self, version: PromptVersion) -> None:
        async with self._db.connection() as conn:
            await conn.execute(
                _PROMPT_SAVE,
                version.name,
                version.version,
                version.model_dump_json(),
                str(version.stage),
            )

    async def get(self, name: str, version: int) -> PromptVersion | None:
        async with self._db.connection() as conn:
            row = await conn.fetchrow(_PROMPT_GET, name, version)
        return PromptVersion.model_validate_json(row["artifact"]) if row else None

    async def versions(self, name: str) -> list[PromptVersion]:
        async with self._db.connection() as conn:
            rows = await conn.fetch(_PROMPT_VERSIONS, name)
        return [PromptVersion.model_validate_json(r["artifact"]) for r in rows]

    async def names(self) -> list[str]:
        async with self._db.connection() as conn:
            rows = await conn.fetch(_PROMPT_NAMES)
        return [r["name"] for r in rows]


class PostgresBudgetStore:
    def __init__(self, database: Database) -> None:
        self._db = database

    async def get(self, tenant_id: UUID) -> TenantBudget | None:
        async with self._db.connection() as conn:
            row = await conn.fetchrow(_BUDGET_GET, tenant_id)
        return TenantBudget.model_validate_json(row["budget"]) if row else None

    async def save(self, budget: TenantBudget) -> None:
        async with self._db.connection() as conn:
            await conn.execute(_BUDGET_UPSERT, budget.tenant_id, budget.model_dump_json())

    async def all(self) -> list[TenantBudget]:
        async with self._db.connection() as conn:
            rows = await conn.fetch(_BUDGET_ALL)
        return [TenantBudget.model_validate_json(r["budget"]) for r in rows]


class PostgresResidencyStore:
    def __init__(self, database: Database) -> None:
        self._db = database

    async def get(self, tenant_id: UUID) -> str | None:
        async with self._db.connection() as conn:
            row = await conn.fetchrow(_RESIDENCY_GET, tenant_id)
        return row["region"] if row else None

    async def set(self, tenant_id: UUID, region: str) -> None:
        async with self._db.connection() as conn:
            await conn.execute(_RESIDENCY_UPSERT, tenant_id, region)

    async def all(self) -> dict[UUID, str]:
        async with self._db.connection() as conn:
            rows = await conn.fetch(_RESIDENCY_ALL)
        return {r["tenant_id"]: r["region"] for r in rows}
