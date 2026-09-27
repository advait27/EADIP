"""Integration tests for the audit hash chain (SEC-08) against real Postgres.

Skipped automatically when no database is reachable, like test_postgres_rls.
The log is exercised as the least-privilege app role; tampering is simulated
the way a privileged attacker would do it: as the owner, disabling the
append-only trigger and editing rows.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest_asyncio

from eadip.adapters.postgres import Database
from eadip.adapters.postgres_audit_log import PostgresAuditLog
from eadip.security.audit import GENESIS_HASH, make_event
from tests.integration.pg import ADMIN_DSN, app_database


@pytest_asyncio.fixture
async def db() -> Database:
    database = await app_database("TRUNCATE audit_event RESTART IDENTITY")
    yield database
    await database.close()


async def _tamper(db: Database, tenant_id: UUID, sql: str) -> None:
    """Edit rows as the owner with the trigger off (the app role cannot)."""
    admin = Database(ADMIN_DSN)
    await admin.connect()
    try:
        async with admin.tenant_connection(tenant_id) as conn:
            await conn.execute("ALTER TABLE audit_event DISABLE TRIGGER audit_event_append_only")
            try:
                await conn.execute(sql)
            finally:
                await conn.execute("ALTER TABLE audit_event ENABLE TRIGGER audit_event_append_only")
    finally:
        await admin.close()


async def test_chain_verifies_after_jsonb_round_trip(db: Database) -> None:
    tenant = uuid4()
    log = PostgresAuditLog(db)
    # Key order, float spelling and nesting are all normalized by JSONB.
    detail = {"z": 1e20, "a": 1.0, "m": {"y": 0.1, "x": [1, 2.5, None, True]}, "s": "é"}
    for i in range(3):
        await log.record(make_event(tenant_id=tenant, actor="u", action=f"a{i}", detail=detail))
    report = await log.verify(tenant)
    assert report.ok, report
    assert report.checked == 3
    assert report.anchored


async def test_chains_are_per_tenant(db: Database) -> None:
    ta, tb = uuid4(), uuid4()
    log = PostgresAuditLog(db)
    await log.record(make_event(tenant_id=ta, actor="u", action="a"))
    await log.record(make_event(tenant_id=tb, actor="u", action="b"))
    async with db.tenant_connection(tb) as conn:
        prev = await conn.fetchval("SELECT prev_hash FROM audit_event WHERE tenant_id = $1", tb)
    assert prev == GENESIS_HASH
    assert (await log.verify(ta)).ok
    assert (await log.verify(tb)).ok


async def test_verify_detects_edit_that_bypasses_trigger(db: Database) -> None:
    tenant = uuid4()
    log = PostgresAuditLog(db)
    for i in range(3):
        await log.record(make_event(tenant_id=tenant, actor="u", action=f"a{i}"))
    await _tamper(db, tenant, "UPDATE audit_event SET detail = '{\"x\": 1}' WHERE action = 'a1'")
    report = await log.verify(tenant)
    assert not report.ok
    assert report.break_index == 1


async def test_verify_detects_deleted_middle_row(db: Database) -> None:
    tenant = uuid4()
    log = PostgresAuditLog(db)
    for i in range(3):
        await log.record(make_event(tenant_id=tenant, actor="u", action=f"a{i}"))
    await _tamper(db, tenant, "DELETE FROM audit_event WHERE action = 'a1'")
    report = await log.verify(tenant)
    assert not report.ok
    assert report.break_index == 1


async def test_verify_skips_pre_chain_rows(db: Database) -> None:
    tenant = uuid4()
    async with db.tenant_connection(tenant) as conn:
        await conn.execute(
            "INSERT INTO audit_event (tenant_id, actor, action) VALUES ($1, 'u', 'legacy')",
            tenant,
        )
    log = PostgresAuditLog(db)
    await log.record(make_event(tenant_id=tenant, actor="u", action="chained"))
    report = await log.verify(tenant)
    assert report.ok
    assert report.checked == 1
