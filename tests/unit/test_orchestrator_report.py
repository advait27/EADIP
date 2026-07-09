"""The engine's terminal verify+report stage (Phase 7): it produces a brief,
persists it on the state, and streams the verification events."""

from __future__ import annotations

import importlib.util
from uuid import uuid4

import pytest

from eadip.agents.router import Router
from eadip.domain.entities import RunStatus
from eadip.orchestrator.checkpoint import InMemoryCheckpointer
from eadip.orchestrator.models import Finding, Goal, Plan, PlanStep, Reflection, StepResult
from eadip.orchestrator.service import OrchestratorService
from eadip.orchestrator.state import RunState
from eadip.verification.models import (
    ExecutiveBrief,
    Recommendation,
    VerificationReport,
    VerificationStatus,
    VerifiedClaim,
)

_DUCKDB = importlib.util.find_spec("duckdb") is not None


class _Interp:
    async def interpret(self, question: str) -> Goal:
        return Goal(objective=question, metrics=["margin"])


class _Planner:
    async def plan(self, goal: Goal, gaps: list[str]) -> Plan:
        return Plan(steps=[PlanStep(id="a", kind="x", description="a")])


class _Exec:
    kind = "x"

    async def execute(self, step: PlanStep, state: RunState) -> StepResult:
        return StepResult(
            step_id=step.id,
            kind=step.kind,
            ok=True,
            findings=[Finding(claim="c", source="analytics", step_id=step.id)],
        )


class _Sufficient:
    async def reflect(self, goal, findings, results) -> Reflection:  # type: ignore[no-untyped-def]
        return Reflection(sufficient=True)


class StubReporter:
    def __init__(self) -> None:
        claim = VerifiedClaim(
            claim="EMEA gross margin fell by 230",
            source="analytics",
            kind="headline",
            status=VerificationStatus.VERIFIED,
            method="recompute",
            confidence=0.8,
        )
        self._report = VerificationReport.from_claims([claim])
        self._brief = ExecutiveBrief(
            question="q",
            headline="EMEA gross margin fell by 230",
            overall_confidence=0.8,
            key_findings=[claim],
            recommendations=[
                Recommendation(action="act", rationale="r", impact=230, confidence=0.8)
            ],
        )
        self.called_with: dict = {}

    async def produce(self, findings, *, tenant_id, question, objective, stop_reason=None):  # type: ignore[no-untyped-def]
        self.called_with = {"findings": len(findings), "objective": objective}
        return self._report, self._brief


async def test_engine_runs_verification_and_streams_brief() -> None:
    reporter = StubReporter()
    svc = OrchestratorService(
        interpreter=_Interp(),
        planner=_Planner(),
        router=Router(),
        reflection=_Sufficient(),
        executors={"x": _Exec()},
        checkpointer=InMemoryCheckpointer(),
        reporter=reporter,
    )
    state = RunState(
        run_id=uuid4(), tenant_id=uuid4(), user_id=uuid4(), question="why did EMEA margin fall?"
    )
    types = [e.type async for e in svc.stream(state)]

    assert reporter.called_with["objective"] == "why did EMEA margin fall?"
    assert state.brief is not None and state.brief.headline == "EMEA gross margin fell by 230"
    assert len(state.verified_claims) == 1
    for t in ("claim.verified", "verification.summary", "recommendation", "brief"):
        assert t in types
    assert state.status == RunStatus.DONE


@pytest.mark.skipif(not _DUCKDB, reason="duckdb (data extra) not installed")
async def test_end_to_end_verified_brief_matches_recomputation() -> None:
    from eadip.adapters.duckdb_warehouse import DuckDBWarehouse
    from eadip.adapters.memory_vector_store import InMemoryVectorStore
    from eadip.analytics.factory import build_analytics_service
    from eadip.config.settings import Settings
    from eadip.orchestrator.factory import build_orchestrator
    from eadip.retrieval.factory import build_retrieval_service

    settings = Settings()
    orch = build_orchestrator(
        settings,
        retrieval_service=build_retrieval_service(settings, InMemoryVectorStore()),
        analytics_service=build_analytics_service(settings, DuckDBWarehouse()),
        warehouse=DuckDBWarehouse(),
        checkpointer=InMemoryCheckpointer(),
    )
    state = RunState(
        run_id=uuid4(),
        tenant_id=uuid4(),
        user_id=uuid4(),
        question="Why did EMEA gross margin fall last quarter?",
    )
    await orch.run(state)

    assert state.brief is not None
    assert "EMEA" in state.brief.headline
    # Every claim was independently re-derived and matched (no conflicts).
    assert state.verified_claims
    assert all(c.status == VerificationStatus.VERIFIED for c in state.verified_claims)
    headline = next(c for c in state.verified_claims if c.kind == "headline")
    assert headline.claimed_magnitude == headline.recomputed_magnitude
