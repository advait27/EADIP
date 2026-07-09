"""Step executors (FR-005, AP-8): turn a PlanStep into a tool call against the
Phase 4 retrieval engine or the Phase 5 analytics engine, and normalise the
result into findings with provenance. The Execution Coordinator (in the engine)
drives these with retry/timeout/idempotency.

An executor may also expose ``preflight`` (Phase 9): resolve what the step would
do and, when it is side-effecting, return the exact ``ProposedAction`` so the
engine can pause for human approval before it runs (the interrupt_before gate).
Retrieval/analytics are read-only, so they do not implement it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from eadip.analytics.models import AnalyticsRequest
from eadip.analytics.service import AnalyticsService
from eadip.orchestrator.models import Evidence, Finding, PlanStep, StepResult
from eadip.orchestrator.state import RunState
from eadip.retrieval.models import Query
from eadip.retrieval.service import RetrievalService

if TYPE_CHECKING:
    from eadip.approval.models import ProposedAction

# Nominal per-step compute cost (Phase 11, FR-053): keeps per-run cost tracked
# (and tenant budgets meaningful) even on the offline/heuristic path, where no
# LLM tokens are billed. LLM-backed steps add their real token cost on top.
RETRIEVAL_STEP_COST_USD = 0.0005
ANALYTICS_STEP_COST_USD = 0.001


def _clip(text: str, n: int = 240) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1] + "…"


class Executor(Protocol):
    kind: str

    async def execute(self, step: PlanStep, state: RunState) -> StepResult: ...


@runtime_checkable
class PreflightExecutor(Protocol):
    """An executor that can describe a step's side-effecting action before it runs
    (Phase 9). Returns None when the step would have no gated effect."""

    async def preflight(self, step: PlanStep, state: RunState) -> ProposedAction | None: ...


class RetrievalExecutor:
    kind = "retrieve"

    def __init__(self, service: RetrievalService, top_k: int = 5) -> None:
        self._svc = service
        self._top_k = top_k

    async def execute(self, step: PlanStep, state: RunState) -> StepResult:
        query = Query(
            tenant_id=state.tenant_id,
            text=str(step.params.get("query", state.question)),
            acl_tags=state.acl_tags,
            top_k=int(step.params.get("top_k", self._top_k)),
            effort=str(step.params.get("effort", "standard")),
        )
        result = await self._svc.retrieve(query)
        findings = [
            Finding(
                claim=_clip(p.text),
                source="retrieval",
                step_id=step.id,
                kind="passage",
                evidence=[Evidence(kind="passage", ref=p.source_ref, snippet=_clip(p.text, 160))],
            )
            for p in result.passages[: self._top_k]
        ]
        return StepResult(
            step_id=step.id,
            kind=self.kind,
            ok=True,
            summary=f"retrieved {len(result.passages)} passage(s)",
            findings=findings,
            cost_usd=RETRIEVAL_STEP_COST_USD,
        )


class AnalyticsExecutor:
    kind = "analyze"

    def __init__(self, service: AnalyticsService) -> None:
        self._svc = service

    async def execute(self, step: PlanStep, state: RunState) -> StepResult:
        result = await self._svc.analyze(
            AnalyticsRequest(
                tenant_id=state.tenant_id,
                question=str(step.params.get("question", state.question)),
                metric=step.params.get("metric"),
                dimension=step.params.get("dimension"),
                filter_value=step.params.get("filter_value"),
                effort=str(step.params.get("effort", "standard")),
            )
        )
        findings: list[Finding] = []
        driver_sql = next((q.sql for q in result.queries if q.purpose == "drivers"), None)
        if result.verified and driver_sql:
            findings.append(
                Finding(
                    claim=result.headline,
                    source="analytics",
                    step_id=step.id,
                    kind="headline",
                    magnitude=sum(d.delta for d in result.drivers),
                    evidence=[Evidence(kind="query", ref=driver_sql)],
                    detail={"total": sum(d.delta for d in result.drivers)},
                )
            )
        for f in result.findings:
            findings.append(
                Finding(
                    claim=f.statement,
                    source="analytics",
                    step_id=step.id,
                    kind=f.kind,
                    magnitude=f.magnitude,
                    association_only=f.association_only,
                    evidence=[Evidence(kind="query", ref=f.evidence_sql, snippet=f.kind)],
                    detail=dict(f.detail),
                )
            )
        ok = result.verified or bool(result.findings)
        return StepResult(
            step_id=step.id,
            kind=self.kind,
            ok=ok,
            summary=result.headline or "no metric movement found",
            findings=findings,
            cost_usd=ANALYTICS_STEP_COST_USD,
        )
