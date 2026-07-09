"""SQL-result cache (FR-020). Keyed on (tenant, exact validated SQL) so a result
is reused only for an identical, identically-scoped query. In-memory by default;
a Redis-backed cache can drop in behind the same Protocol later."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from eadip.ports.warehouse import QueryResult


class SqlResultCache(Protocol):
    async def get(self, tenant_id: UUID, sql: str) -> QueryResult | None: ...

    async def put(self, tenant_id: UUID, sql: str, result: QueryResult) -> None: ...


class InMemorySqlResultCache:
    def __init__(self) -> None:
        self._store: dict[tuple[UUID, str], QueryResult] = {}

    async def get(self, tenant_id: UUID, sql: str) -> QueryResult | None:
        return self._store.get((tenant_id, sql))

    async def put(self, tenant_id: UUID, sql: str, result: QueryResult) -> None:
        self._store[(tenant_id, sql)] = result


class NullSqlResultCache:
    """No-op cache (caching disabled)."""

    async def get(self, tenant_id: UUID, sql: str) -> QueryResult | None:
        return None

    async def put(self, tenant_id: UUID, sql: str, result: QueryResult) -> None:
        return None
