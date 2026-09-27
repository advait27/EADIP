"""VerificationService: re-execute source query + recompute → verified /
conflicting / unverified (never silently reconciled), plus corroboration, the
``value_checked`` label, and the snapshot commitment."""

from __future__ import annotations

from uuid import UUID

from eadip.adapters.demo_finance import FINANCE_SCHEMA
from eadip.orchestrator.models import Evidence, Finding
from eadip.ports.warehouse import QueryResult, WarehouseError, WarehouseSchema, query_result
from eadip.verification.commitment import commit_rows
from eadip.verification.models import VerificationStatus
from eadip.verification.verifier import VerificationService

T = UUID("11111111-1111-1111-1111-111111111111")
_DRIVER_SQL = (
    f"SELECT product_line, period, SUM(revenue - cogs) AS gross_margin "
    f"FROM finance_metrics WHERE tenant_id = '{T}' AND region = 'EMEA' "
    f"AND period IN ('2026-Q1', '2026-Q2') GROUP BY product_line, period"
)


class FakeWarehouse:
    def __init__(self, result: QueryResult | None = None, error: bool = False) -> None:
        self._result = result
        self._error = error
        self.executed: list[str] = []

    async def schema(self, tenant_id: UUID) -> WarehouseSchema:
        return FINANCE_SCHEMA

    async def execute(
        self, tenant_id: UUID, sql: str, *, row_cap: int, timeout_s: float
    ) -> QueryResult:
        self.executed.append(sql)
        if self._error:
            raise WarehouseError("boom")
        assert self._result is not None
        return self._result


def _driver_finding(magnitude: float) -> Finding:
    return Finding(
        claim=f"product_line 'Hardware' contributed {magnitude:+.0f} of the gross margin change",
        source="analytics",
        kind="driver",
        magnitude=magnitude,
        detail={"member": "Hardware"},
        evidence=[Evidence(kind="query", ref=_DRIVER_SQL)],
    )


_FRESH = query_result(
    ("product_line", "period", "gross_margin"),
    [("Hardware", "2026-Q1", 400.0), ("Hardware", "2026-Q2", 180.0)],
)


async def test_verified_when_recomputation_matches() -> None:
    svc = VerificationService(warehouse=FakeWarehouse(_FRESH))
    report = await svc.verify([_driver_finding(-220.0)], tenant_id=T)
    claim = report.claims[0]
    assert claim.status == VerificationStatus.VERIFIED
    assert claim.method == "recompute"
    assert claim.recomputed_magnitude == -220.0
    assert claim.value_checked is True


async def test_conflicting_when_recomputation_disagrees() -> None:
    # Fresh data now says Hardware delta is -100, but the claim said -220.
    svc = VerificationService(warehouse=FakeWarehouse(_FRESH))
    report = await svc.verify([_driver_finding(-100.0)], tenant_id=T)
    assert report.conflicting == 1
    assert report.claims[0].status == VerificationStatus.CONFLICTING
    assert "re-derived" in report.claims[0].note  # not silently reconciled
    assert report.claims[0].value_checked is True


async def test_unverified_when_query_cannot_be_reexecuted() -> None:
    svc = VerificationService(warehouse=FakeWarehouse(error=True))
    report = await svc.verify([_driver_finding(-220.0)], tenant_id=T)
    assert report.claims[0].status == VerificationStatus.UNVERIFIED
    assert report.claims[0].value_checked is False
    assert report.snapshot_commitments == {}  # row fetch failed too: nothing committed
    assert report.snapshot_notes and "not committed" in report.snapshot_notes[0]


async def test_unverified_when_sql_fails_fresh_revalidation() -> None:
    bad = Finding(
        claim="x",
        source="analytics",
        kind="driver",
        magnitude=1.0,
        evidence=[Evidence(kind="query", ref="DELETE FROM finance_metrics")],
    )
    svc = VerificationService(warehouse=FakeWarehouse(_FRESH))
    report = await svc.verify([bad], tenant_id=T)
    assert report.claims[0].status == VerificationStatus.UNVERIFIED
    assert "re-validation" in report.claims[0].note


async def test_retrieval_corroboration_raises_confidence() -> None:
    driver = _driver_finding(-220.0)
    corroborator = Finding(
        claim="EMEA Hardware gross margin fell on higher COGS",
        source="retrieval",
        kind="passage",
        evidence=[
            Evidence(kind="passage", ref="finance/emea", snippet="hardware margin cogs emea")
        ],
    )
    svc = VerificationService(warehouse=FakeWarehouse(_FRESH))
    report = await svc.verify([driver, corroborator], tenant_id=T)
    analytics_claim = next(c for c in report.claims if c.source == "analytics")
    assert analytics_claim.corroborating_sources >= 1
    assert analytics_claim.confidence > 0.7  # base verified + corroboration boost


async def test_reproduced_but_not_recomputable_is_unverified() -> None:
    # R2: the query re-runs, but a driver with no member cannot be recomputed —
    # reproducing a query is not the same as checking its number.
    finding = _driver_finding(-220.0)
    finding.detail = {}
    svc = VerificationService(warehouse=FakeWarehouse(_FRESH))
    claim = (await svc.verify([finding], tenant_id=T)).claims[0]
    assert claim.status == VerificationStatus.UNVERIFIED
    assert claim.method == "requery"
    assert claim.value_checked is False
    assert claim.recomputed_magnitude is None
    assert "not independently recomputed" in claim.note


async def test_no_claimed_magnitude_is_unverified() -> None:
    # R3: a recomputed value with nothing to compare it to is not a verification.
    finding = _driver_finding(-220.0)
    finding.magnitude = None
    svc = VerificationService(warehouse=FakeWarehouse(_FRESH))
    claim = (await svc.verify([finding], tenant_id=T)).claims[0]
    assert claim.status == VerificationStatus.UNVERIFIED
    assert claim.method == "recompute"
    assert claim.recomputed_magnitude == -220.0
    assert claim.value_checked is False
    assert "no magnitude" in claim.note


async def test_retrieval_and_tool_claims_are_not_value_checked() -> None:
    passage = Finding(
        claim="memo",
        source="retrieval",
        kind="passage",
        evidence=[Evidence(kind="passage", ref="doc://memo")],
    )
    tool = Finding(
        claim="fx rate", source="tool", kind="tool", evidence=[Evidence(kind="tool", ref="fx.rate")]
    )
    svc = VerificationService(warehouse=FakeWarehouse(_FRESH))
    report = await svc.verify([passage, tool], tenant_id=T)
    assert [c.status for c in report.claims] == [VerificationStatus.VERIFIED] * 2
    assert not any(c.value_checked for c in report.claims)


async def test_snapshot_commitment_recorded_for_touched_tables() -> None:
    wh = FakeWarehouse(_FRESH)
    report = await VerificationService(warehouse=wh).verify([_driver_finding(-220.0)], tenant_id=T)
    assert report.snapshot_commitments == {"finance_metrics": commit_rows(_FRESH.rows)}
    # The snapshot fetch is the bundle's plain projection: tenant-scoped, no GROUP BY.
    fetch = wh.executed[-1]
    assert "GROUP BY" not in fetch and f"tenant_id = '{T}'" in fetch


async def test_truncated_snapshot_is_not_committed() -> None:
    capped = QueryResult(columns=_FRESH.columns, rows=_FRESH.rows, truncated=True)
    report = await VerificationService(warehouse=FakeWarehouse(capped)).verify(
        [_driver_finding(-220.0)], tenant_id=T
    )
    assert report.snapshot_commitments == {}
    assert any("row cap" in n for n in report.snapshot_notes)
