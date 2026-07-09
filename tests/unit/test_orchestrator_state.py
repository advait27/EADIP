"""RunState reducer + helpers (the parallel-branch merge must be deterministic)."""

from __future__ import annotations

from uuid import uuid4

from eadip.domain.entities import RunStatus
from eadip.orchestrator.models import Finding, Goal, Plan, PlanStep, StepResult
from eadip.orchestrator.state import RunState


def _state() -> RunState:
    return RunState(run_id=uuid4(), tenant_id=uuid4(), user_id=uuid4(), question="q")


def test_apply_step_results_merges_in_deterministic_order() -> None:
    st = _state()
    # Results arrive out of order (as parallel branches would); reducer sorts by id.
    st.apply_step_results(
        [
            StepResult(
                step_id="b",
                kind="x",
                ok=True,
                cost_usd=0.5,
                findings=[Finding(claim="from b", source="analytics", step_id="b")],
            ),
            StepResult(
                step_id="a",
                kind="x",
                ok=True,
                cost_usd=0.25,
                findings=[Finding(claim="from a", source="retrieval", step_id="a")],
            ),
        ]
    )
    assert st.completed_step_ids == ["a", "b"]
    assert [f.claim for f in st.findings] == ["from a", "from b"]
    assert st.cost_usd == 0.75


def test_apply_step_results_collects_errors_and_dedups_completed() -> None:
    st = _state()
    st.completed_step_ids = ["a"]
    st.apply_step_results([StepResult(step_id="a", kind="x", ok=False, error="boom")])
    assert st.completed_step_ids == ["a"]  # not duplicated
    assert st.errors == ["a: boom"]


def test_pending_steps_and_resumable() -> None:
    st = _state()
    assert st.pending_steps() == []  # no plan yet
    st.plan = Plan(
        steps=[
            PlanStep(id="a", kind="x", description="a"),
            PlanStep(id="b", kind="x", description="b"),
        ]
    )
    st.completed_step_ids = ["a"]
    assert st.pending_steps() == ["b"]

    assert st.is_resumable() is False  # no goal yet
    st.goal = Goal(objective="q")
    assert st.is_resumable() is True
    st.status = RunStatus.DONE
    assert st.is_resumable() is False
