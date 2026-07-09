"""Integration tests proving the Phase 2 storage guarantees against real Postgres.

Skipped automatically when no database is reachable (e.g. local dev without
docker). CI runs these with a Postgres service. Set EADIP_TEST_POSTGRES_DSN to
point at a throwaway database.
"""

from __future__ import annotations

import os
from uuid import UUID, uuid4

import pytest
import pytest_asyncio

from eadip.adapters.migrations import apply_migrations
from eadip.adapters.postgres import Database
from eadip.adapters.postgres_audit_log import PostgresAuditLog
from eadip.adapters.postgres_run_repository import PostgresRunRepository
from eadip.domain.entities import Run
from eadip.security.audit import make_event

DSN = os.environ.get("EADIP_TEST_POSTGRES_DSN", "postgresql://eadip:eadip@localhost:5432/eadip")


@pytest_asyncio.fixture
async def db() -> Database:
    database = Database(DSN)
    try:
        await database.connect()
        await database.ping()
    except Exception:  # connection refused / driver error -> not available here
        pytest.skip("Postgres not available for integration tests")
    await apply_migrations(database)
    async with database.connection() as conn:
        await conn.execute(
            "TRUNCATE run, audit_event, user_role, permission, app_user, role, tenant "
            "RESTART IDENTITY CASCADE"
        )
    yield database
    await database.close()


async def _seed_tenant_user(database: Database) -> tuple[UUID, UUID]:
    tenant_id, user_id = uuid4(), uuid4()
    async with database.connection() as conn:
        await conn.execute("INSERT INTO tenant (id, name) VALUES ($1, $2)", tenant_id, "t")
        await conn.execute(
            "INSERT INTO app_user (id, tenant_id, email, sso_subject) VALUES ($1, $2, $3, $4)",
            user_id,
            tenant_id,
            f"{user_id}@example.com",
            str(user_id),
        )
    return tenant_id, user_id


async def test_rls_isolates_tenants(db: Database) -> None:
    ta, ua = await _seed_tenant_user(db)
    tb, ub = await _seed_tenant_user(db)
    repo_a = PostgresRunRepository(db, ta)
    repo_b = PostgresRunRepository(db, tb)
    run_a = Run(tenant_id=ta, user_id=ua, question="A")
    run_b = Run(tenant_id=tb, user_id=ub, question="B")
    await repo_a.add(run_a)
    await repo_b.add(run_b)

    assert await repo_a.get(run_a.id) is not None
    # RLS makes tenant B's run invisible to tenant A — and vice-versa.
    assert await repo_a.get(run_b.id) is None
    assert await repo_b.get(run_a.id) is None


async def test_audit_event_is_append_only(db: Database) -> None:
    import asyncpg

    ta, ua = await _seed_tenant_user(db)
    log = PostgresAuditLog(db)
    await log.record(make_event(tenant_id=ta, actor=str(ua), action="run.create"))

    with pytest.raises(asyncpg.PostgresError):
        async with db.tenant_connection(ta) as conn:
            await conn.execute("UPDATE audit_event SET action = 'tampered'")

    with pytest.raises(asyncpg.PostgresError):
        async with db.tenant_connection(ta) as conn:
            await conn.execute("DELETE FROM audit_event")
