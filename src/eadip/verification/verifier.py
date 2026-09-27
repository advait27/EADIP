"""Verification Agent (FR-041, AP-1): re-run and re-compute every number.

What this checks, precisely. For each analytics claim it re-validates the
*recorded source SQL* through the safety gate (a fresh safety check), re-executes
that same SQL on the same warehouse the run used, and recomputes the reported
magnitude from the fresh rows (``recompute.py``, which reuses the Phase 5
statistics functions for forecasts/correlations/anomalies). It then compares:

  - match → VERIFIED (``value_checked``);
  - disagree, or an anomaly no longer flagged → **CONFLICTING**, never silently
    reconciled (``value_checked``);
  - the query reproduces but the number cannot be recomputed from it, or the
    claim carries no magnitude to compare → UNVERIFIED (reproduced, not checked);
  - cannot re-validate / re-run / no rows → UNVERIFIED.

What is independent: the stored number is not trusted — it is recomputed from a
fresh execution and compared, in a fresh context. What is NOT independent: the
same SQL on the same warehouse (so a query that answers the wrong question, or
wrong source data, reproduces faithfully) and, for some kinds, the same
statistics code. This establishes reproducibility and arithmetic, not that the
query is the right one.

Retrieval and tool claims are VERIFIED on their source pointer (grounding /
call provenance) — they are NOT value-checked. Retrieval claims also serve as
cross-source corroboration for analytics claims.

For every table an analytics claim touched, the tenant's rows (the same governed
projection the evidence bundle serves) are hashed into a snapshot commitment
(``commitment.py``) so a later bundle can show whether its rows still match.
"""

from __future__ import annotations

from uuid import UUID

from eadip.analytics.models import QueryPlan
from eadip.analytics.sql_generator import TemplateSqlGenerator
from eadip.analytics.sql_validator import SqlSafetyValidator, SqlValidationError
from eadip.orchestrator.models import Finding
from eadip.ports.warehouse import Warehouse, WarehouseError, WarehouseSchema
from eadip.retrieval.text import content_tokens
from eadip.verification.commitment import commit_rows
from eadip.verification.confidence import score
from eadip.verification.models import (
    NOTE_NO_MAGNITUDE,
    NOTE_NOT_RECOMPUTED,
    VerificationReport,
    VerificationStatus,
    VerifiedClaim,
)
from eadip.verification.recompute import anomaly_still_flagged, recompute_magnitude


class VerificationService:
    def __init__(
        self,
        *,
        warehouse: Warehouse,
        row_cap: int = 10000,
        timeout_s: float = 5.0,
        max_join_tables: int = 4,
        rel_tolerance: float = 0.02,
    ) -> None:
        self._wh = warehouse
        self._row_cap = row_cap
        self._timeout = timeout_s
        self._max_join_tables = max_join_tables
        self._tol = rel_tolerance

    async def verify(self, findings: list[Finding], *, tenant_id: UUID) -> VerificationReport:
        analytics = [f for f in findings if f.source == "analytics"]
        retrieval = [f for f in findings if f.source == "retrieval"]
        tool = [f for f in findings if f.source == "tool"]
        claims: list[VerifiedClaim] = []
        commitments: dict[str, str] = {}
        notes: list[str] = []
        if analytics:
            schema = await self._wh.schema(tenant_id)
            validator = SqlSafetyValidator(
                schema, dialect="duckdb", max_join_tables=self._max_join_tables
            )
            touched: set[str] = set()
            for f in analytics:
                claims.append(
                    await self._verify_analytics(f, validator, tenant_id, retrieval, touched)
                )
            commitments, notes = await self._commit_snapshots(touched, schema, validator, tenant_id)
        for f in retrieval:
            claims.append(self._verify_retrieval(f))
        for f in tool:
            claims.append(self._verify_tool(f))
        return VerificationReport.from_claims(
            claims, snapshot_commitments=commitments, snapshot_notes=notes
        )

    # --- snapshot commitment: hash the rows the bundle will later serve --------
    async def _commit_snapshots(
        self,
        tables: set[str],
        schema: WarehouseSchema,
        validator: SqlSafetyValidator,
        tenant_id: UUID,
    ) -> tuple[dict[str, str], list[str]]:
        """Same plan as the evidence bundle's row fetch (all columns, no GROUP BY),
        so the served rows can be compared with what verification saw. The hash
        comes from this server; it detects later change, not a consistent lie."""
        commitments: dict[str, str] = {}
        notes: list[str] = []
        for name in sorted(tables):
            table = schema.table(name)
            if table is None:
                continue
            plan = QueryPlan(
                purpose="bundle",
                table=table.name,
                dimensions=tuple(c.name for c in table.columns),
                group_by=False,
            )
            try:
                sql = TemplateSqlGenerator.render(plan, schema.tenant_column, tenant_id)
                validated = validator.validate(sql, tenant_id=tenant_id)
                result = await self._wh.execute(
                    tenant_id, validated.sql, row_cap=self._row_cap, timeout_s=self._timeout
                )
            except (SqlValidationError, WarehouseError) as exc:
                notes.append(f"{name}: not committed ({exc})")
                continue
            if result.truncated:
                notes.append(f"{name}: not committed (row cap {self._row_cap} hit)")
                continue
            commitments[table.name] = commit_rows(result.rows)
        return commitments, notes

    # --- analytics: re-execute source query + recompute -----------------------
    async def _verify_analytics(
        self,
        finding: Finding,
        validator: SqlSafetyValidator,
        tenant_id: UUID,
        retrieval: list[Finding],
        touched: set[str],
    ) -> VerifiedClaim:
        corro = self._corroboration(finding, retrieval)
        sql = finding.evidence[0].ref if finding.evidence else ""
        if not sql:
            return self._claim(
                finding, VerificationStatus.UNVERIFIED, "none", None, corro, "no query"
            )

        try:  # fresh safety re-validation — the stored SQL must still pass the gate
            validated = validator.validate(sql, tenant_id=tenant_id)
        except SqlValidationError as exc:
            return self._claim(
                finding,
                VerificationStatus.UNVERIFIED,
                "requery",
                None,
                corro,
                f"failed re-validation: {exc.reason}",
            )
        touched.update(validated.tables)
        try:
            result = await self._wh.execute(
                tenant_id, validated.sql, row_cap=self._row_cap, timeout_s=self._timeout
            )
        except WarehouseError as exc:
            return self._claim(
                finding,
                VerificationStatus.UNVERIFIED,
                "requery",
                None,
                corro,
                f"exec failed: {exc}",
            )
        if result.row_count == 0:
            return self._claim(
                finding, VerificationStatus.UNVERIFIED, "requery", None, corro, "no rows on re-run"
            )

        recomputed = recompute_magnitude(finding, result)
        if recomputed is None:
            # The query ran, but nothing was compared: reproducible, not checked.
            return self._claim(
                finding,
                VerificationStatus.UNVERIFIED,
                "requery",
                None,
                corro,
                NOTE_NOT_RECOMPUTED,
            )
        if finding.kind == "anomaly" and not anomaly_still_flagged(finding, result):
            return self._claim(
                finding,
                VerificationStatus.CONFLICTING,
                "recompute",
                recomputed,
                corro,
                "point is no longer anomalous on re-derivation",
                value_checked=True,
            )
        if finding.magnitude is None:
            return self._claim(
                finding,
                VerificationStatus.UNVERIFIED,
                "recompute",
                recomputed,
                corro,
                NOTE_NO_MAGNITUDE,
            )
        if self._close(recomputed, finding.magnitude):
            return self._claim(
                finding,
                VerificationStatus.VERIFIED,
                "recompute",
                recomputed,
                corro,
                "",
                value_checked=True,
            )
        return self._claim(
            finding,
            VerificationStatus.CONFLICTING,
            "recompute",
            recomputed,
            corro,
            f"claimed {finding.magnitude:.4g}, re-derived {recomputed:.4g}",
            value_checked=True,
        )

    # --- retrieval: grounding extract + corroboration -------------------------
    def _verify_retrieval(self, finding: Finding) -> VerifiedClaim:
        has_source = bool(finding.evidence and finding.evidence[0].ref)
        status = VerificationStatus.VERIFIED if has_source else VerificationStatus.UNVERIFIED
        return VerifiedClaim(
            claim=finding.claim,
            source="retrieval",
            kind=finding.kind,
            status=status,
            method="source_extract",
            confidence=score(status, corroborating_sources=0, association_only=False),
            provenance=[e.ref for e in finding.evidence],
            note="grounding extract with a source pointer (pointer checked, value not)",
            value_checked=False,
        )

    # --- tool: external reference data, verified by call provenance -----------
    def _verify_tool(self, finding: Finding) -> VerifiedClaim:
        """A governed tool result is external, UNTRUSTED data (SEC-06): we verify
        that it has an auditable provenance pointer (server.tool the call went to),
        but we do NOT independently re-derive an external system's response — that
        limitation is stated in the note and the confidence is not inflated."""
        has_source = bool(finding.evidence and finding.evidence[0].ref)
        status = VerificationStatus.VERIFIED if has_source else VerificationStatus.UNVERIFIED
        return VerifiedClaim(
            claim=finding.claim,
            source="tool",
            kind=finding.kind,
            status=status,
            method="tool_provenance",
            confidence=score(status, corroborating_sources=0, association_only=False),
            provenance=[e.ref for e in finding.evidence],
            note="external tool output (untrusted data); provenance recorded, not re-derived",
            value_checked=False,
        )

    def _corroboration(self, finding: Finding, retrieval: list[Finding]) -> int:
        keys = set(content_tokens(finding.claim))
        if not keys:
            return 0
        count = 0
        for r in retrieval:
            snippet = r.evidence[0].snippet if r.evidence else ""
            toks = set(content_tokens(r.claim)) | set(content_tokens(snippet))
            if len(keys & toks) >= 2:
                count += 1
        return count

    def _close(self, a: float, b: float) -> bool:
        return abs(a - b) <= self._tol * max(1.0, abs(b))

    def _claim(
        self,
        finding: Finding,
        status: VerificationStatus,
        method: str,
        recomputed: float | None,
        corroboration: int,
        note: str,
        *,
        value_checked: bool = False,
    ) -> VerifiedClaim:
        return VerifiedClaim(
            claim=finding.claim,
            source="analytics",
            kind=finding.kind,
            status=status,
            method=method,
            confidence=score(
                status,
                corroborating_sources=corroboration,
                association_only=finding.association_only,
            ),
            association_only=finding.association_only,
            claimed_magnitude=finding.magnitude,
            recomputed_magnitude=recomputed,
            corroborating_sources=corroboration,
            provenance=[e.ref for e in finding.evidence],
            note=note,
            value_checked=value_checked,
            detail=dict(finding.detail),
        )
