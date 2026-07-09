"""Async PostgreSQL access (asyncpg).

The key primitive is `tenant_connection`: a transaction with `app.tenant_id`
set, so Row-Level Security scopes every statement to one tenant (AP-5, SEC-09).
`asyncpg` is imported lazily so importing this module is cheap and the no-database
dev path needs no driver at runtime.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID


class Database:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._pool: Any | None = None

    async def connect(self) -> None:
        import asyncpg

        if self._pool is None:
            self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=10)

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    @property
    def pool(self) -> Any:
        if self._pool is None:
            raise RuntimeError("Database not connected; call connect() first")
        return self._pool

    async def ping(self) -> bool:
        async with self.pool.acquire() as conn:
            return bool(await conn.fetchval("SELECT 1") == 1)

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[Any]:
        async with self.pool.acquire() as conn:
            yield conn

    @asynccontextmanager
    async def tenant_connection(self, tenant_id: UUID) -> AsyncIterator[Any]:
        """A transaction scoped to one tenant for RLS. `set_config(..., true)`
        makes the setting local to this transaction only."""
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(tenant_id))
                yield conn
