"""Shared Postgres setup for the integration tests.

Schema changes and TRUNCATE run as the owner (ADMIN_DSN); the code under test
connects as the least-privilege app role (DSN), because Row-Level Security does
not bind a superuser, a BYPASSRLS role or the table owner. Testing as the owner
would make every tenant-isolation assertion vacuous.
"""

from __future__ import annotations

import os

import pytest

from eadip.adapters.migrations import apply_migrations
from eadip.adapters.postgres import Database

DSN = os.environ.get(
    "EADIP_TEST_POSTGRES_DSN", "postgresql://eadip_app:eadip_app@localhost:5432/eadip"
)
ADMIN_DSN = os.environ.get(
    "EADIP_TEST_POSTGRES_ADMIN_DSN", "postgresql://eadip:eadip@localhost:5432/eadip"
)


async def app_database(truncate_sql: str) -> Database:
    """Migrate + reset as the owner, then return a connected app-role Database
    (skips the test when Postgres or the app role is unavailable)."""
    admin = Database(ADMIN_DSN)
    try:
        await admin.connect()
        await admin.ping()
    except Exception:  # connection refused / driver error -> not available here
        pytest.skip("Postgres not available for integration tests")
    try:
        await apply_migrations(admin)
        async with admin.connection() as conn:
            await conn.execute(truncate_sql)
    finally:
        await admin.close()
    database = Database(DSN)
    try:
        await database.connect()
        await database.ping()
    except Exception:
        pytest.skip("app role not available (see deploy/postgres/app_role.sql)")
    return database
