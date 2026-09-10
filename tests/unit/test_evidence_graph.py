"""build_evidence_graph (Glass Box): stable ids, every relation, no dangling edges."""

from __future__ import annotations

from uuid import uuid4

from eadip.domain.entities import RunStatus
from eadip.orchestrator.graph import build_evidence_graph, evidence_id
from eadip.orchestrator.models import Evidence, Finding, Goal, Plan, PlanStep
from eadip.orchestrator.state import RunState
from eadip.verification.models import (
    ExecutiveBrief,
    Recommendation,
    VerificationStatus,
    VerifiedClaim,
)

SQL = (
    "SELECT product_line, period, SUM(revenue - cogs) AS gross_margin "
    "FROM finance_metrics WHERE tenant_id = 't'"
)


def _state(*, verified: bool = True, brief: bool = True) -> RunState:
    state = RunState(
        run_id=uuid4(), tenant_id=uuid4(), user_id=uuid4(), question="Why did EMEA margin fall?"
    )
    state.goal = Goal(objective="Why did EMEA margin fall?", metrics=["margin"], entities=["EMEA"])
    state.plan = Plan(
        steps=[
            PlanStep(id="retrieve-context", kind="retrieve", description="Retrieve context"),
            PlanStep(
                id="analyze-metric",
                kind="analyze",
                description="Analyze margin",
                depends_on=["retrieve-context"],
            ),
        ]
    )
    state.completed_step_ids = ["retrieve-context", "analyze-metric"]
    state.findings = [
        Finding(
            claim="Q2 board memo: hardware COGS spiked",
            source="retrieval",
            step_id="retrieve-context",
            kind="passage",
            evidence=[Evidence(kind="passage", ref="doc://memo#3", snippet="hardware COGS spiked")],
        ),
        Finding(
            claim="EMEA gross margin fell by 230",
            source="analytics",
            step_id="analyze-metric",
            kind="headline",
            magnitude=-230.0,
            evidence=[Evidence(kind="query", ref=SQL)],
            detail={"total": -230.0},
        ),
        Finding(
            claim="Hardware contributed -220",
            source="analytics",
            step_id="analyze-metric",
            kind="driver",
            magnitude=-220.0,
            evidence=[Evidence(kind="query", ref=SQL, snippet="driver")],
        ),
    ]
    if verified:
        state.verified_claims = [
            VerifiedClaim(
                claim="EMEA gross margin fell by 230",
                source="analytics",
                kind="headline",
                status=VerificationStatus.VERIFIED,
                method="recompute",
                confidence=0.9,
                claimed_magnitude=-230.0,
                recomputed_magnitude=-230.0,
                provenance=[SQL],
            ),
            VerifiedClaim(
                claim="Hardware contributed -220",
                source="analytics",
                kind="driver",
                status=VerificationStatus.CONFLICTING,
                method="recompute",
                confidence=0.3,
                claimed_magnitude=-220.0,
                recomputed_magnitude=-100.0,
                provenance=[SQL],
            ),
            VerifiedClaim(
                claim="Q2 board memo: hardware COGS spiked",
                source="retrieval",
                kind="passage",
                status=VerificationStatus.VERIFIED,
                method="source_extract",
                confidence=0.7,
                provenance=["doc://memo#3"],
            ),
        ]
    if brief:
        state.brief = ExecutiveBrief(
            question=state.question,
            headline="EMEA gross margin fell by 230",
            overall_confidence=0.8,
            recommendations=[
                Recommendation(
                    action="Remediate Hardware",
                    rationale="largest driver",
                    impact=220,
                    confidence=0.9,
                    based_on=["Hardware contributed -220"],
                ),
            ],
        )
    state.status = RunStatus.DONE
    return state


def test_graph_has_every_kind_and_relation() -> None:
    g = build_evidence_graph(_state())
    kinds = {n.kind for n in g.nodes}
    assert kinds == {"question", "goal", "step", "finding", "claim", "evidence", "recommendation"}
    relations = {e.relation for e in g.edges}
    assert relations == {"asks", "plans", "produces", "cites", "verifies", "supports"}


def test_no_dangling_edges_and_stable_ids() -> None:
    g = build_evidence_graph(_state())
    ids = g.node_ids()
    for e in g.edges:
        assert e.source in ids and e.target in ids, e
    assert "goal" in ids and "step:analyze-metric" in ids and "finding:1" in ids
    assert "claim:0" in ids and "rec:0" in ids
    assert evidence_id("query", SQL) in ids
    # Two findings citing the same SQL share ONE evidence node.
    assert sum(1 for n in g.nodes if n.kind == "evidence") == 2


def test_claims_join_to_findings_on_text_and_provenance() -> None:
    g = build_evidence_graph(_state())
    verifies = {(e.source, e.target) for e in g.edges if e.relation == "verifies"}
    assert ("claim:0", "finding:1") in verifies
    assert ("claim:1", "finding:2") in verifies
    assert ("claim:2", "finding:0") in verifies
    claim1 = next(n for n in g.nodes if n.id == "claim:1")
    assert claim1.data["status"] == "conflicting"
    assert claim1.data["recomputed_magnitude"] == -100.0


def test_recommendations_support_claims() -> None:
    g = build_evidence_graph(_state())
    assert ("rec:0", "claim:1") in {
        (e.source, e.target) for e in g.edges if e.relation == "supports"
    }


def test_partial_state_mid_run_is_a_valid_graph() -> None:
    state = _state(verified=False, brief=False)
    state.status = RunStatus.EXECUTING
    state.completed_step_ids = ["retrieve-context"]
    g = build_evidence_graph(state)
    assert {n.kind for n in g.nodes} == {"question", "goal", "step", "finding", "evidence"}
    step = next(n for n in g.nodes if n.id == "step:analyze-metric")
    assert step.data["completed"] is False
    assert step.data["depends_on"] == ["retrieve-context"]


def test_fresh_state_is_just_the_question() -> None:
    state = RunState(run_id=uuid4(), tenant_id=uuid4(), user_id=uuid4(), question="q")
    g = build_evidence_graph(state)
    assert [n.id for n in g.nodes] == ["question"] and g.edges == []


def test_unmatched_claims_and_based_on_do_not_create_edges() -> None:
    state = _state()
    state.verified_claims.append(
        VerifiedClaim(
            claim="orphan",
            source="analytics",
            status=VerificationStatus.UNVERIFIED,
            method="none",
            confidence=0.1,
        )
    )
    state.brief.recommendations.append(  # type: ignore[union-attr]
        Recommendation(action="x", rationale="y", impact=1, confidence=0.5, based_on=["nope"])
    )
    g = build_evidence_graph(state)
    assert "claim:3" in g.node_ids() and "rec:1" in g.node_ids()
    assert not [e for e in g.edges if e.source in ("claim:3", "rec:1")]
