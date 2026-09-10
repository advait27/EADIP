"""Evidence bundle (Glass Box): everything a READER needs to re-run the numbers.

For each verified analytics claim the bundle carries the exact provenance SQL
(freshly re-validated through the safety gate) and, for every table that SQL
touches, the tenant's rows. The row fetch is rendered by the same template
generator and validated by the same gate as any other analytics query — the
bundle never introduces a new read path. The browser loads the rows into
DuckDB-WASM, runs each claim's SQL and compares with the claimed magnitude
using the server's own tolerance.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from eadip.analytics.models import QueryPlan
from eadip.analytics.sql_generator import TemplateSqlGenerator
from eadip.analytics.sql_validator import SqlSafetyValidator, SqlValidationError
from eadip.orchestrator.state import RunState
from eadip.ports.warehouse import Warehouse, WarehouseError


class BundleColumn(BaseModel):
    name: str
    type: str  # "text" | "number" | "date"


class BundleTable(BaseModel):
    name: str
    columns: list[BundleColumn]
    rows: list[list[Any]]
    row_count: int
    truncated: bool = False  # row cap hit: client-side aggregates are unreliable


class BundleClaim(BaseModel):
    index: int  # position in RunState.verified_claims / the graph's claim:<n>
    claim: str
    kind: str
    status: str
    sql: str
    tables: list[str]
    claimed_magnitude: float | None = None
    recomputed_magnitude: float | None = None
    detail: dict[str, Any] = Field(default_factory=dict)


class SkippedClaim(BaseModel):
    index: int
    reason: str


class EvidenceBundle(BaseModel):
    run_id: UUID
    rel_tolerance: float
    row_cap: int
    claims: list[BundleClaim] = Field(default_factory=list)
    tables: list[BundleTable] = Field(default_factory=list)
    skipped: list[SkippedClaim] = Field(default_factory=list)


async def build_evidence_bundle(
    state: RunState,
    warehouse: Warehouse,
    *,
    row_cap: int,
    rel_tolerance: float,
    timeout_s: float = 5.0,
    max_join_tables: int = 4,
) -> EvidenceBundle:
    tenant = state.tenant_id
    bundle = EvidenceBundle(run_id=state.run_id, rel_tolerance=rel_tolerance, row_cap=row_cap)
    schema = await warehouse.schema(tenant)
    validator = SqlSafetyValidator(schema, dialect="duckdb", max_join_tables=max_join_tables)

    needed: set[str] = set()
    for index, claim in enumerate(state.verified_claims):
        if claim.source != "analytics":
            continue
        if not claim.provenance:
            bundle.skipped.append(SkippedClaim(index=index, reason="no provenance"))
            continue
        try:
            validated = validator.validate(claim.provenance[0], tenant_id=tenant)
        except SqlValidationError as exc:
            bundle.skipped.append(SkippedClaim(index=index, reason=f"sql rejected: {exc.reason}"))
            continue
        bundle.claims.append(
            BundleClaim(
                index=index,
                claim=claim.claim,
                kind=claim.kind,
                status=str(claim.status),
                sql=validated.sql,
                tables=list(validated.tables),
                claimed_magnitude=claim.claimed_magnitude,
                recomputed_magnitude=claim.recomputed_magnitude,
                detail=dict(claim.detail),
            )
        )
        needed.update(validated.tables)

    for name in sorted(needed):
        table = schema.table(name)
        if table is None:  # cannot happen after validation; defensive
            continue
        plan = QueryPlan(
            purpose="bundle",
            table=table.name,
            dimensions=tuple(c.name for c in table.columns),
            group_by=False,
        )
        sql = TemplateSqlGenerator.render(plan, schema.tenant_column, tenant)
        validated = validator.validate(sql, tenant_id=tenant)
        try:
            result = await warehouse.execute(
                tenant, validated.sql, row_cap=row_cap, timeout_s=timeout_s
            )
        except WarehouseError as exc:
            for c in bundle.claims:
                if name in c.tables:
                    bundle.skipped.append(SkippedClaim(index=c.index, reason=f"rows: {exc}"))
            continue
        bundle.tables.append(
            BundleTable(
                name=table.name,
                columns=[BundleColumn(name=c.name, type=c.type) for c in table.columns],
                rows=[list(r) for r in result.rows],
                row_count=result.row_count,
                truncated=result.truncated,
            )
        )
    return bundle
