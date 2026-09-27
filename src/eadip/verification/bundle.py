"""Evidence bundle (Glass Box): everything a READER needs to re-run the numbers.

For each verified analytics claim the bundle carries the exact provenance SQL
(freshly re-validated through the safety gate) and, for every table that SQL
touches, the tenant's rows. The row fetch is rendered by the same template
generator and validated by the same gate as any other analytics query — the
bundle never introduces a new read path. The browser loads the rows into
DuckDB-WASM, runs each claim's SQL and compares with the claimed magnitude
using the server's own tolerance.

What the browser check is: an independent re-execution *of rows this server
supplied*. It confirms the claim's arithmetic over those rows; it cannot show the
rows are the tenant's real data.

Snapshot commitment. Each table carries ``sha256`` — the hash of its rows as
served, in the canonical form of ``commitment.py`` — and the bundle carries the
``snapshot_commitments`` recorded when the run was verified.
``matches_commitment`` says whether the served rows still hash to the recorded
value (None when no commitment was recorded, e.g. pre-commitment runs or a
truncated table). This detects rows that changed between verification and
sharing, and lets a reader compare against a commitment obtained through another
channel (e.g. the run's ``verification.summary`` event, or a copy taken at the
time). It does NOT protect against a server that lies consistently at
verification time: the commitment comes from the same server as the rows.
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
from eadip.verification.commitment import commit_rows


class BundleColumn(BaseModel):
    name: str
    type: str  # "text" | "number" | "date"


class BundleTable(BaseModel):
    name: str
    columns: list[BundleColumn]
    rows: list[list[Any]]
    row_count: int
    truncated: bool = False  # row cap hit: client-side aggregates are unreliable
    sha256: str = ""  # canonical hash of ``rows`` as served (commitment.py)
    # served hash == the commitment recorded at verification; None when none recorded
    matches_commitment: bool | None = None


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
    # table -> sha256 recorded when the run was verified (from the same server)
    snapshot_commitments: dict[str, str] = Field(default_factory=dict)


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
    bundle = EvidenceBundle(
        run_id=state.run_id,
        rel_tolerance=rel_tolerance,
        row_cap=row_cap,
        snapshot_commitments=dict(getattr(state, "snapshot_commitments", None) or {}),
    )
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
        rows = [list(r) for r in result.rows]
        digest = commit_rows(rows)
        committed = bundle.snapshot_commitments.get(table.name)
        bundle.tables.append(
            BundleTable(
                name=table.name,
                columns=[BundleColumn(name=c.name, type=c.type) for c in table.columns],
                rows=rows,
                row_count=result.row_count,
                truncated=result.truncated,
                sha256=digest,
                matches_commitment=None if committed is None else digest == committed,
            )
        )
    return bundle
