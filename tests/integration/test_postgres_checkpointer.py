"""Postgres checkpointer: RunState round-trips and is tenant-isolated by RLS
(Phase 6 resume-on-crash). Skipped when no database is reachable; CI runs it."""

from __future__ import annotations

import os
from uuid import UUID, uuid4

import pytest
import pytest_asyncio

from eadip.adapters.migrations import apply_migrations
from eadip.adapters.postgres import Database
from eadip.adapters.postgres_checkpointer import PostgresCheckpointer
from eadip.domain.entities import RunStatus
from eadip.orchestrator.models import Finding, Goal, Plan, PlanStep
from eadip.orchestrator.state import RunState

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
        await conn.execute("TRUNCATE run_checkpoint, tenant RESTART IDENTITY CASCADE")
    yield database
    await database.close()


async def _tenant(database: Database) -> UUID:
    tid = uuid4()
    async with database.connection() as conn:
        await conn.execute("INSERT INTO tenant (id, name) VALUES ($1, $2)", tid, "t")
    return tid


def _state(tenant_id: UUID, run_id: UUID) -> RunState:
    return RunState(
        run_id=run_id,
        tenant_id=tenant_id,
        user_id=uuid4(),
        question="why did EMEA margin fall?",
        status=RunStatus.EXECUTING,
        goal=Goal(objective="why did EMEA margin fall?", metrics=["margin"]),
        plan=Plan(steps=[PlanStep(id="a", kind="retrieve", description="ground")]),
        completed_step_ids=["a"],
        findings=[Finding(claim="EMEA margin fell 230", source="analytics", step_id="a")],
        iterations=1,
    )


async def test_checkpoint_round_trips(db: Database) -> None:
    tenant = await _tenant(db)
    run_id = uuid4()
    cp = PostgresCheckpointer(db)
    await cp.save(_state(tenant, run_id))

    loaded = await cp.load(tenant, run_id)
    assert loaded is not None
    assert loaded.status == RunStatus.EXECUTING
    assert loaded.completed_step_ids == ["a"]
    assert loaded.findings[0].claim == "EMEA margin fell 230"
    assert loaded.goal is not None and loaded.goal.metrics == ["margin"]


async def test_checkpoint_upsert_and_tenant_isolation(db: Database) -> None:
    ta = await _tenant(db)
    tb = await _tenant(db)
    run_id = uuid4()
    cp = PostgresCheckpointer(db)

    state = _state(ta, run_id)
    await cp.save(state)
    state.iterations = 5  # update in place + re-save => upsert
    await cp.save(state)
    assert (await cp.load(ta, run_id)).iterations == 5  # type: ignore[union-attr]

    # Tenant B cannot read tenant A's checkpoint (RLS).
    assert await cp.load(tb, run_id) is None
