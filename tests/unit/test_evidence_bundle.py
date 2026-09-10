"""Evidence bundle (Glass Box): provenance SQL re-validated, tenant rows fetched
through the governed path, no GROUP BY dedup, truncation surfaced."""

from __future__ import annotations

from uuid import UUID, uuid4

from eadip.adapters.demo_finance import FINANCE_SCHEMA, demo_finance_rows
from eadip.orchestrator.state import RunState
from eadip.ports.warehouse import QueryResult, WarehouseError, WarehouseSchema
from eadip.verification.bundle import build_evidence_bundle
from eadip.verification.models import VerificationStatus, VerifiedClaim


class FakeWarehouse:
    def __init__(self, *, truncated: bool = False, fail: bool = False) -> None:
        self.executed: list[str] = []
        self._truncated = truncated
        self._fail = fail

    async def schema(self, tenant_id: UUID) -> WarehouseSchema:
        return FINANCE_SCHEMA

    async def execute(
        self, tenant_id: UUID, sql: str, *, row_cap: int, timeout_s: float
    ) -> QueryResult:
        self.executed.append(sql)
        if self._fail:
            raise WarehouseError("boom")
        rows = demo_finance_rows(tenant_id)
        # Duplicate one row: the bundle must NOT collapse duplicates.
        rows = rows + [rows[0]]
        return QueryResult(
            columns=("tenant_id", "region", "product_line", "period", "revenue", "cogs", "opex"),
            rows=tuple(rows[:row_cap]),
            truncated=self._truncated,
        )


def _sql(tenant: UUID) -> str:
    return (
        "SELECT product_line, period, SUM(revenue - cogs) AS gross_margin FROM finance_metrics "
        f"WHERE tenant_id = '{tenant}' AND region = 'EMEA' GROUP BY product_line, period"
    )


def _state() -> RunState:
    state = RunState(run_id=uuid4(), tenant_id=uuid4(), user_id=uuid4(), question="q")
    t = state.tenant_id
    state.verified_claims = [
        VerifiedClaim(
            claim="headline",
            source="analytics",
            kind="headline",
            status=VerificationStatus.VERIFIED,
            method="recompute",
            confidence=0.9,
            claimed_magnitude=-230.0,
            recomputed_magnitude=-230.0,
            provenance=[_sql(t)],
            detail={"total": -230.0},
        ),
        VerifiedClaim(
            claim="memo",
            source="retrieval",
            kind="passage",
            status=VerificationStatus.VERIFIED,
            method="source_extract",
            confidence=0.7,
            provenance=["doc://memo"],
        ),
        VerifiedClaim(
            claim="bad sql",
            source="analytics",
            kind="driver",
            status=VerificationStatus.UNVERIFIED,
            method="requery",
            confidence=0.1,
            provenance=["DROP TABLE finance_metrics"],
        ),
        VerifiedClaim(
            claim="no sql",
            source="analytics",
            kind="driver",
            status=VerificationStatus.UNVERIFIED,
            method="none",
            confidence=0.1,
        ),
        VerifiedClaim(
            claim="other tenant",
            source="analytics",
            kind="driver",
            status=VerificationStatus.UNVERIFIED,
            method="requery",
            confidence=0.1,
            provenance=[_sql(uuid4())],
        ),
    ]
    return state


async def test_bundle_carries_validated_sql_and_tenant_rows() -> None:
    wh = FakeWarehouse()
    state = _state()
    b = await build_evidence_bundle(state, wh, row_cap=10000, rel_tolerance=0.02)
    assert b.run_id == state.run_id and b.rel_tolerance == 0.02 and b.row_cap == 10000
    assert [c.index for c in b.claims] == [0]
    assert b.claims[0].tables == ["finance_metrics"]
    assert b.claims[0].detail == {"total": -230.0}
    assert "GROUP BY" in b.claims[0].sql  # the claim's own SQL is untouched
    assert sorted((s.index, s.reason.split(":")[0]) for s in b.skipped) == [
        (2, "sql rejected"),
        (3, "no provenance"),
        (4, "sql rejected"),
    ]
    # The row fetch: explicit columns incl. tenant_id, tenant literal, NO GROUP BY.
    assert len(wh.executed) == 1
    fetch = wh.executed[0]
    assert "GROUP BY" not in fetch and "*" not in fetch
    assert f"tenant_id = '{state.tenant_id}'" in fetch
    [table] = b.tables
    assert table.name == "finance_metrics"
    assert [c.name for c in table.columns][0] == "tenant_id"
    assert table.row_count == 55  # 54 demo rows + the duplicate survives
    assert table.truncated is False


async def test_truncation_is_surfaced() -> None:
    b = await build_evidence_bundle(
        _state(), FakeWarehouse(truncated=True), row_cap=10, rel_tolerance=0.02
    )
    assert b.tables[0].truncated is True and b.tables[0].row_count == 10


async def test_row_fetch_failure_skips_dependent_claims() -> None:
    b = await build_evidence_bundle(
        _state(), FakeWarehouse(fail=True), row_cap=100, rel_tolerance=0.02
    )
    assert b.tables == []
    assert any(s.index == 0 and s.reason.startswith("rows:") for s in b.skipped)


async def test_empty_state_gives_empty_bundle() -> None:
    wh = FakeWarehouse()
    state = RunState(run_id=uuid4(), tenant_id=uuid4(), user_id=uuid4(), question="q")
    b = await build_evidence_bundle(state, wh, row_cap=100, rel_tolerance=0.02)
    assert b.claims == [] and b.tables == [] and wh.executed == []
