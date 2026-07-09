"""Postgres-backed episodic + semantic memory stores with RLS (Phase 11, FR-045).

Same ports as the in-memory reference. Episodes upsert on (tenant, question_key)
so a repeat question refreshes its episode; semantic facts are de-duplicated on
the (tenant, subject, predicate, object) triple so consolidation stays
idempotent. `forget` implements the tenant erasure cascade (GDPR) — it runs
inside the tenant's RLS context, so it can only ever erase that tenant's rows.
"""

from __future__ import annotations

from uuid import UUID

from eadip.adapters.postgres import Database
from eadip.memory.models import EpisodicMemory, SemanticFact

_EPISODE_UPSERT = (
    "INSERT INTO episodic_memory (id, tenant_id, question_key, memory, updated_at) "
    "VALUES ($1, $2, $3, $4::jsonb, now()) "
    "ON CONFLICT (tenant_id, question_key) DO UPDATE SET "
    "memory = EXCLUDED.memory, updated_at = now()"
)
_EPISODE_FIND = "SELECT memory FROM episodic_memory WHERE question_key = $1"
_EPISODE_TOUCH = (
    "UPDATE episodic_memory SET "
    "memory = jsonb_set(memory, '{hits}', (COALESCE((memory->>'hits')::int, 0) + 1)::text::jsonb) "
    "WHERE id = $1"
)
_EPISODE_COUNT = "SELECT count(*) AS n FROM episodic_memory"
_EPISODE_FORGET = "DELETE FROM episodic_memory"

_FACT_INSERT = (
    "INSERT INTO semantic_fact (id, tenant_id, subject, predicate, object, fact) "
    "VALUES ($1, $2, $3, $4, $5, $6::jsonb) "
    "ON CONFLICT (tenant_id, subject, predicate, object) DO NOTHING"
)
_FACT_ALL = "SELECT fact FROM semantic_fact ORDER BY created_at"
_FACT_COUNT = "SELECT count(*) AS n FROM semantic_fact"
_FACT_FORGET = "DELETE FROM semantic_fact"


class PostgresEpisodicStore:
    def __init__(self, database: Database) -> None:
        self._db = database

    async def add(self, memory: EpisodicMemory) -> None:
        async with self._db.tenant_connection(memory.tenant_id) as conn:
            await conn.execute(
                _EPISODE_UPSERT,
                memory.id,
                memory.tenant_id,
                memory.question_key,
                memory.model_dump_json(),
            )

    async def find(self, tenant_id: UUID, question_key: str) -> EpisodicMemory | None:
        async with self._db.tenant_connection(tenant_id) as conn:
            row = await conn.fetchrow(_EPISODE_FIND, question_key)
        if row is None:
            return None
        return EpisodicMemory.model_validate_json(row["memory"])

    async def touch(self, tenant_id: UUID, memory_id: UUID) -> None:
        async with self._db.tenant_connection(tenant_id) as conn:
            await conn.execute(_EPISODE_TOUCH, memory_id)

    async def count(self, tenant_id: UUID) -> int:
        async with self._db.tenant_connection(tenant_id) as conn:
            row = await conn.fetchrow(_EPISODE_COUNT)
        return int(row["n"]) if row else 0

    async def forget(self, tenant_id: UUID) -> int:
        async with self._db.tenant_connection(tenant_id) as conn:
            result = await conn.execute(_EPISODE_FORGET)
        return int(result.split()[-1]) if result else 0


class PostgresSemanticStore:
    def __init__(self, database: Database) -> None:
        self._db = database

    async def add(self, fact: SemanticFact) -> None:
        async with self._db.tenant_connection(fact.tenant_id) as conn:
            await conn.execute(
                _FACT_INSERT,
                fact.id,
                fact.tenant_id,
                fact.subject,
                fact.predicate,
                fact.object,
                fact.model_dump_json(),
            )

    async def all(self, tenant_id: UUID) -> list[SemanticFact]:
        async with self._db.tenant_connection(tenant_id) as conn:
            rows = await conn.fetch(_FACT_ALL)
        return [SemanticFact.model_validate_json(r["fact"]) for r in rows]

    async def count(self, tenant_id: UUID) -> int:
        async with self._db.tenant_connection(tenant_id) as conn:
            row = await conn.fetchrow(_FACT_COUNT)
        return int(row["n"]) if row else 0

    async def forget(self, tenant_id: UUID) -> int:
        async with self._db.tenant_connection(tenant_id) as conn:
            result = await conn.execute(_FACT_FORGET)
        return int(result.split()[-1]) if result else 0
