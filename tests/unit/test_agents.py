"""Deterministic agents: Goal Interpreter, Planner, Router, Reflection."""

from __future__ import annotations

from eadip.agents.interpreter import HeuristicGoalInterpreter
from eadip.agents.planner import HeuristicPlanner
from eadip.agents.reflection import HeuristicReflection
from eadip.agents.router import Router
from eadip.orchestrator.models import Finding, Goal, Plan, PlanStep, StepResult


async def test_goal_interpreter_extracts_metric_entity_complexity() -> None:
    goal = await HeuristicGoalInterpreter().interpret("Why did EMEA margin fall last quarter?")
    assert "margin" in goal.metrics
    assert "EMEA" in goal.entities
    assert goal.complexity == "deep"  # "why"/"fall" -> deep
    simple = await HeuristicGoalInterpreter().interpret("list tenants")
    assert simple.complexity == "simple"


async def test_planner_grounds_quantifies_and_turns_gaps_into_steps() -> None:
    goal = Goal(objective="why did EMEA margin fall?", metrics=["margin"], entities=["EMEA"])
    plan = await HeuristicPlanner().plan(goal, [])
    kinds = {s.kind for s in plan.steps}
    assert kinds == {"retrieve", "analyze"}
    analyze = next(s for s in plan.steps if s.kind == "analyze")
    assert analyze.params["filter_value"] == "EMEA"

    replan = await HeuristicPlanner().plan(goal, ["no grounding evidence retrieved"])
    assert any(s.id.startswith("retrieve-gap-") for s in replan.steps)
    assert replan.signature() != plan.signature()  # different -> not a loop


async def test_planner_adds_governed_tool_step_only_on_explicit_signal() -> None:
    # No external-data signal -> no tool step (existing plans are unchanged).
    plain = Goal(objective="why did EMEA margin fall?", metrics=["margin"])
    assert not any(s.kind == "tool" for s in (await HeuristicPlanner().plan(plain, [])).steps)

    # Currency signal + an extractable code -> a governed tool step with args.
    fx = Goal(objective="EUR exchange rate effect on EMEA margin", metrics=["margin"])
    plan = await HeuristicPlanner().plan(fx, [])
    tool = next(s for s in plan.steps if s.kind == "tool")
    assert tool.params["arguments"] == {"currency": "EUR"}
    assert "governed tool" in plan.rationale

    # Signal but no suppliable argument -> no tool step (won't call blind).
    vague = Goal(objective="the currency situation is complex", metrics=["margin"])
    assert not any(s.kind == "tool" for s in (await HeuristicPlanner().plan(vague, [])).steps)


async def test_planner_adds_write_tool_step_on_remediation_signal() -> None:
    # An explicit "open a ticket" ask -> a side-effecting tool step (Phase 9); it
    # will pause for approval at run time. The heuristic never fabricates a write.
    goal = Goal(objective="why did margin fall? then open a ticket to fix it", metrics=["margin"])
    plan = await HeuristicPlanner().plan(goal, [])
    ticket = next(s for s in plan.steps if s.id == "tool-open-ticket")
    assert ticket.kind == "tool"
    assert ticket.params["arguments"]["priority"] == "high"

    no_action = Goal(objective="why did margin fall?", metrics=["margin"])
    assert not any(
        s.id == "tool-open-ticket" for s in (await HeuristicPlanner().plan(no_action, [])).steps
    )


def test_router_batches_by_deps_and_caps_parallelism() -> None:
    router = Router()
    plan = Plan(
        steps=[
            PlanStep(id="a", kind="x", description="a"),
            PlanStep(id="b", kind="x", description="b"),
            PlanStep(id="c", kind="x", description="c", depends_on=["a"]),
        ]
    )
    first = router.next_batch(plan, set(), max_parallelism=2)
    assert [s.id for s in first] == ["a", "b"]  # independent, capped, sorted
    after = router.next_batch(plan, {"a", "b"}, max_parallelism=2)
    assert [s.id for s in after] == ["c"]  # now unblocked


def test_router_detects_unsatisfiable_deps() -> None:
    router = Router()
    plan = Plan(steps=[PlanStep(id="x", kind="x", description="x", depends_on=["missing"])])
    assert router.is_blocked(plan, set()) is True


async def test_reflection_sufficient_and_gaps() -> None:
    goal = Goal(objective="q", metrics=["margin"])
    findings = [
        Finding(claim="EMEA margin fell by 230", source="analytics"),
        Finding(claim="grounding passage", source="retrieval"),
    ]
    ok = await HeuristicReflection().reflect(
        goal, findings, [StepResult(step_id="a", kind="x", ok=True)]
    )
    assert ok.sufficient and not ok.should_replan

    insufficient = await HeuristicReflection().reflect(goal, [], [])
    assert not insufficient.sufficient
    assert insufficient.should_replan
    assert "no grounding evidence retrieved" in insufficient.gaps


async def test_heuristic_agents_label_their_output_heuristic() -> None:
    from eadip.verification.models import VerificationStatus, VerifiedClaim
    from eadip.verification.recommend import HeuristicRecommender

    goal = await HeuristicGoalInterpreter().interpret("Why did EMEA margin fall?")
    plan = await HeuristicPlanner().plan(goal, [])
    reflection = await HeuristicReflection().reflect(goal, [], [])
    driver = VerifiedClaim(
        claim="Hardware contributed -220",
        source="analytics",
        kind="driver",
        status=VerificationStatus.VERIFIED,
        method="recompute",
        confidence=0.9,
        claimed_magnitude=-220.0,
    )
    recs = await HeuristicRecommender().recommend("o", [driver])
    assert recs
    for out in (goal, plan, reflection, *recs):
        assert out.produced_by == "heuristic"
        assert out.fallback_reason is None
