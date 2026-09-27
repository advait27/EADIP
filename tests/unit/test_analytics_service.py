"""AnalyticsService orchestration: the repair loop, the safety guarantee at the
service layer (non-read-only SQL never reaches the warehouse), and end-to-end
metric-movement explanation with per-finding provenance (Phase 5 DoD)."""

from __future__ import annotations

import importlib.util
from uuid import UUID, uuid4

import pytest

from eadip.analytics.cache import NullSqlResultCache
from eadip.analytics.models import AnalyticsRequest, QueryPlan
from eadip.analytics.service import AnalyticsService
from eadip.analytics.sql_validator import SqlSafetyValidator
from eadip.ports.warehouse import (
    ColumnSchema,
    QueryResult,
    TableSchema,
    WarehouseSchema,
    query_result,
)

SCHEMA = WarehouseSchema(
    tables=(
        TableSchema(
            "finance_metrics",
            (
                ColumnSchema("tenant_id", "text"),
                ColumnSchema("product_line", "text"),
                ColumnSchema("period", "text"),
                ColumnSchema("revenue", "number"),
                ColumnSchema("cogs", "number"),
            ),
        ),
    ),
)
T = UUID("11111111-1111-1111-1111-111111111111")
_DUCKDB = importlib.util.find_spec("duckdb") is not None


class FakeWarehouse:
    def __init__(self, result: QueryResult) -> None:
        self._result = result
        self.calls: list[str] = []

    async def schema(self, tenant_id: UUID) -> WarehouseSchema:
        return SCHEMA

    async def execute(
        self, tenant_id: UUID, sql: str, *, row_cap: int, timeout_s: float
    ) -> QueryResult:
        self.calls.append(sql)
        return self._result


class StubGenerator:
    def __init__(self, outputs: list[str]) -> None:
        self._outputs = outputs
        self.calls = 0

    async def generate(self, request, schema, plan, *, tenant_id, feedback=None):  # type: ignore[no-untyped-def]
        out = self._outputs[min(self.calls, len(self._outputs) - 1)]
        self.calls += 1
        return out


def _svc(warehouse: FakeWarehouse, generator: StubGenerator) -> AnalyticsService:
    return AnalyticsService(
        warehouse=warehouse, generator=generator, cache=NullSqlResultCache(), max_repair_attempts=2
    )


_DRIVER_PLAN = QueryPlan(
    purpose="drivers",
    table="finance_metrics",
    dimensions=("product_line",),
    measures=(("SUM(revenue - cogs)", "gross_margin"),),
)
_GOOD = (
    f"SELECT product_line, SUM(revenue - cogs) AS gross_margin "
    f"FROM finance_metrics WHERE tenant_id = '{T}' GROUP BY product_line"
)


async def test_repair_loop_recovers_from_invalid_then_valid_sql() -> None:
    wh = FakeWarehouse(query_result(("product_line", "gross_margin"), [("Hardware", 100.0)]))
    gen = StubGenerator(["DELETE FROM finance_metrics", _GOOD])  # bad, then good
    svc = _svc(wh, gen)
    validator = SqlSafetyValidator(SCHEMA, dialect="duckdb")
    art, result = await svc._run(
        _DRIVER_PLAN, AnalyticsRequest(tenant_id=T, question="q"), SCHEMA, validator
    )
    assert art is not None and art.attempts == 2  # repaired on the second try
    assert result is not None and result.row_count == 1
    assert len(wh.calls) == 1  # only the *valid* SQL ever executed


async def test_non_read_only_sql_never_executes() -> None:
    wh = FakeWarehouse(query_result((), []))
    gen = StubGenerator(["DELETE FROM finance_metrics"])  # always unsafe
    svc = _svc(wh, gen)
    validator = SqlSafetyValidator(SCHEMA, dialect="duckdb")
    art, result = await svc._run(
        _DRIVER_PLAN, AnalyticsRequest(tenant_id=T, question="q"), SCHEMA, validator
    )
    assert art is None and result is None
    assert wh.calls == []  # the gate blocked it before any execution


async def test_sql_artifact_records_which_generator_wrote_it() -> None:
    from eadip.analytics.sql_generator import LLMSqlGenerator, TemplateSqlGenerator

    wh = FakeWarehouse(query_result(("product_line", "gross_margin"), [("Hardware", 100.0)]))
    svc = AnalyticsService(
        warehouse=wh, generator=TemplateSqlGenerator(), cache=NullSqlResultCache()
    )
    validator = SqlSafetyValidator(SCHEMA, dialect="duckdb")
    art, _ = await svc._run(
        _DRIVER_PLAN, AnalyticsRequest(tenant_id=T, question="q"), SCHEMA, validator
    )
    assert art is not None and art.generator == "template"
    assert LLMSqlGenerator.backend == "llm"

    # A generator that does not declare a backend is recorded as unknown, not guessed.
    stub_svc = _svc(wh, StubGenerator([_GOOD]))
    stub_art, _ = await stub_svc._run(
        _DRIVER_PLAN, AnalyticsRequest(tenant_id=T, question="q"), SCHEMA, validator
    )
    assert stub_art is not None and stub_art.generator == "unknown"


@pytest.mark.skipif(not _DUCKDB, reason="duckdb (data extra) not installed")
async def test_end_to_end_metric_movement_with_provenance() -> None:
    from eadip.adapters.duckdb_warehouse import DuckDBWarehouse
    from eadip.analytics.sql_generator import TemplateSqlGenerator

    svc = AnalyticsService(
        warehouse=DuckDBWarehouse(),
        generator=TemplateSqlGenerator(),
        cache=NullSqlResultCache(),
    )
    result = await svc.analyze(
        AnalyticsRequest(tenant_id=uuid4(), question="Why did EMEA gross margin fall last quarter?")
    )
    assert result.verified is True
    assert "EMEA" in result.headline and "margin" in result.headline.lower()
    assert result.drivers[0].label == "Hardware"  # the dominant driver
    assert result.drivers[0].delta < 0

    # Provenance: every finding is bound to a query that was actually executed.
    executed = {q.sql for q in result.queries}
    assert all(q.generator == "template" for q in result.queries)
    assert result.findings
    for f in result.findings:
        assert f.evidence_sql in executed
    # Correlations are labelled association-only (correlation != causation).
    for f in result.findings:
        if f.kind == "correlation":
            assert f.association_only is True
