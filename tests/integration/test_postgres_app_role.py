"""The integration tests' app connection must be one RLS actually binds.

A superuser, a BYPASSRLS role or the table owner sees every tenant's rows, so
the isolation tests would pass or fail for the wrong reason. This fails loudly
when the environment is misconfigured instead of letting them run vacuously.
"""

from __future__ import annotations

from eadip.adapters.postgres import Database
from tests.integration.pg import app_database


async def test_app_connection_is_bound_by_rls() -> None:
    db: Database = await app_database("SELECT 1")
    try:
        async with db.connection() as conn:
            row = await conn.fetchrow(
                "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
            )
            owns = await conn.fetchval(
                "SELECT count(*) FROM pg_tables WHERE schemaname = 'public' "
                "AND tableowner = current_user"
            )
    finally:
        await db.close()
    assert row is not None
    assert row["rolsuper"] is False, "app role is a superuser: RLS does not apply"
    assert row["rolbypassrls"] is False, "app role has BYPASSRLS"
    assert owns == 0, "app role owns tables: only FORCE RLS would bind it"


async def test_app_role_cannot_run_ddl() -> None:
    import asyncpg
    import pytest

    db = await app_database("SELECT 1")
    try:
        with pytest.raises(asyncpg.PostgresError):
            async with db.connection() as conn:
                await conn.execute("ALTER TABLE run DISABLE ROW LEVEL SECURITY")
    finally:
        await db.close()
