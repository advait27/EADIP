"""Analytics route: governed NL->SQL + BI (FR-020..024).

Requires `metrics/read`, runs tenant-scoped, and audits the analysis plus every
SQL statement that executed (the provenance trail Phase 7 verifies against).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from eadip.analytics.models import AnalyticsRequest
from eadip.gateway.authz import require
from eadip.gateway.dependencies import AnalyticsServiceDep, AuditLogDep, ResidencyGuardDep
from eadip.gateway.models import (
    AnalyzeRequest,
    AnalyzeResponse,
    DriverItem,
    FindingItem,
    QueryItem,
)
from eadip.observability.logging import get_logger
from eadip.security.audit import make_event
from eadip.security.identity import Identity
from eadip.security.rbac import Effect

router = APIRouter(prefix="/v1/analytics", tags=["analytics"])
log = get_logger(__name__)

# Querying governed metrics requires read on the metrics domain (analyst holds it).
AnalystDep = Annotated[Identity, Depends(require("metrics", "*", Effect.READ))]


@router.post("", response_model=AnalyzeResponse)
async def analyze(
    body: AnalyzeRequest,
    identity: AnalystDep,
    _residency: ResidencyGuardDep,
    service: AnalyticsServiceDep,
    audit: AuditLogDep,
) -> AnalyzeResponse:
    result = await service.analyze(
        AnalyticsRequest(
            tenant_id=identity.tenant_id,
            question=body.question,
            metric=body.metric,
            dimension=body.dimension,
            filter_column=body.filter_column,
            filter_value=body.filter_value,
            period_column=body.period_column,
            baseline_period=body.baseline_period,
            current_period=body.current_period,
            effort=body.effort,
        )
    )

    actor = str(identity.user_id)
    await audit.record(
        make_event(
            tenant_id=identity.tenant_id,
            actor=actor,
            action="analytics.query",
            detail={
                "question_len": len(body.question),
                "metric": result.metric,
                "verified": result.verified,
                "queries": len(result.queries),
            },
        )
    )
    # Audit each executed statement with its exact SQL (governed provenance).
    for q in result.queries:
        await audit.record(
            make_event(
                tenant_id=identity.tenant_id,
                actor=actor,
                action="sql.execute",
                detail={
                    "purpose": q.purpose,
                    "sql": q.sql,
                    "rows": q.row_count,
                    "truncated": q.truncated,
                    "attempts": q.attempts,
                    "generator": q.generator,
                },
            )
        )
    log.info(
        "analytics.done",
        tenant_id=str(identity.tenant_id),
        verified=result.verified,
        findings=len(result.findings),
    )

    return AnalyzeResponse(
        question=result.question,
        metric=result.metric,
        headline=result.headline,
        verified=result.verified,
        drivers=[
            DriverItem(
                label=d.label,
                baseline=d.baseline,
                current=d.current,
                delta=d.delta,
                share=d.share,
            )
            for d in result.drivers
        ],
        findings=[
            FindingItem(
                kind=f.kind,
                statement=f.statement,
                magnitude=f.magnitude,
                evidence_sql=f.evidence_sql,
                association_only=f.association_only,
                detail=f.detail,
            )
            for f in result.findings
        ],
        queries=[
            QueryItem(
                purpose=q.purpose,
                sql=q.sql,
                row_count=q.row_count,
                truncated=q.truncated,
                attempts=q.attempts,
                generator=q.generator,
            )
            for q in result.queries
        ],
        warnings=result.warnings,
    )
