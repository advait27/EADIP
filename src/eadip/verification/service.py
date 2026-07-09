"""Verification + recommendation + reporting, composed (TAD Ch 8.3).

Takes the findings an orchestrated run collected and produces a verified,
recommendation-bearing executive brief. Depends only on `orchestrator.models`
(a leaf), so it stays decoupled from the engine.
"""

from __future__ import annotations

from uuid import UUID

from eadip.orchestrator.models import Finding
from eadip.verification.models import ExecutiveBrief, VerificationReport
from eadip.verification.recommend import Recommender
from eadip.verification.report import build_executive_brief
from eadip.verification.verifier import VerificationService


class VerificationReportingService:
    def __init__(self, *, verifier: VerificationService, recommender: Recommender) -> None:
        self._verifier = verifier
        self._recommender = recommender

    async def produce(
        self,
        findings: list[Finding],
        *,
        tenant_id: UUID,
        question: str,
        objective: str,
        stop_reason: str | None = None,
    ) -> tuple[VerificationReport, ExecutiveBrief]:
        report = await self._verifier.verify(findings, tenant_id=tenant_id)
        recommendations = await self._recommender.recommend(objective, report.claims)
        brief = build_executive_brief(question, report, recommendations, stop_reason=stop_reason)
        return report, brief
