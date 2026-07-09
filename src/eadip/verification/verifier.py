"""Verification Agent (FR-041, AP-1): independently re-derive every number.

For each analytics claim it re-validates the *exact source SQL* through the
safety gate (a fresh safety check) and re-executes it on the warehouse, then
recomputes the reported magnitude via an independent path and compares:
match → verified, disagree → **conflicting** (never silently reconciled), can't
re-run → unverified. Retrieval claims are grounding extracts (verified by their
source pointer) and also serve as cross-source corroboration for analytics
claims. This runs from a fresh context — it trusts nothing the run collected.
"""

from __future__ import annotations

from uuid import UUID

from eadip.analytics.sql_validator import SqlSafetyValidator, SqlValidationError
from eadip.orchestrator.models import Finding
from eadip.ports.warehouse import Warehouse, WarehouseError
from eadip.retrieval.text import content_tokens
from eadip.verification.confidence import score
from eadip.verification.models import VerificationReport, VerificationStatus, VerifiedClaim
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
        if analytics:
            schema = await self._wh.schema(tenant_id)
            validator = SqlSafetyValidator(
                schema, dialect="duckdb", max_join_tables=self._max_join_tables
            )
            for f in analytics:
                claims.append(await self._verify_analytics(f, validator, tenant_id, retrieval))
        for f in retrieval:
            claims.append(self._verify_retrieval(f))
        for f in tool:
            claims.append(self._verify_tool(f))
        return VerificationReport.from_claims(claims)

    # --- analytics: re-execute source query + recompute -----------------------
    async def _verify_analytics(
        self,
        finding: Finding,
        validator: SqlSafetyValidator,
        tenant_id: UUID,
        retrieval: list[Finding],
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
            return self._claim(
                finding,
                VerificationStatus.VERIFIED,
                "requery",
                None,
                corro,
                "source query reproduced (aggregation not independently recomputed)",
            )
        if finding.kind == "anomaly" and not anomaly_still_flagged(finding, result):
            return self._claim(
                finding,
                VerificationStatus.CONFLICTING,
                "recompute",
                recomputed,
                corro,
                "point is no longer anomalous on re-derivation",
            )
        if finding.magnitude is None or self._close(recomputed, finding.magnitude):
            return self._claim(
                finding, VerificationStatus.VERIFIED, "recompute", recomputed, corro, ""
            )
        return self._claim(
            finding,
            VerificationStatus.CONFLICTING,
            "recompute",
            recomputed,
            corro,
            f"claimed {finding.magnitude:.4g}, re-derived {recomputed:.4g}",
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
            note="grounding extract with a source pointer",
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
        )
