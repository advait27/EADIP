"""Orchestration engine (Phase 6 DoD): multi-step run, re-plan on insufficiency,
streamed progress, resume-after-restart, and every hard bound.

Stub agents/executors keep it deterministic — the engine logic is what's tested.
"""

from __future__ import annotations

from uuid import uuid4

from eadip.agents.router import Router
from eadip.domain.entities import RunStatus
from eadip.orchestrator.checkpoint import InMemoryCheckpointer
from eadip.orchestrator.models import Finding, Goal, Plan, PlanStep, Reflection, StepResult
from eadip.orchestrator.service import OrchestratorService
from eadip.orchestrator.state import RunState


class StubInterpreter:
    async def interpret(self, question: str, *, tenant_id: object = None) -> Goal:
        return Goal(objective=question, metrics=["margin"], entities=["EMEA"], complexity="deep")


class FixedPlanner:
    """Always emits the same 2-step plan (a, then b depends on a)."""

    def __init__(self) -> None:
        self.calls = 0

    async def plan(self, goal: Goal, gaps: list[str]) -> Plan:
        self.calls += 1
        return Plan(
            steps=[
                PlanStep(id="a", kind="x", description="step a"),
                PlanStep(id="b", kind="x", description="step b", depends_on=["a"]),
            ],
            rationale="r",
        )


class RaisingPlanner:
    async def plan(self, goal: Goal, gaps: list[str]) -> Plan:  # pragma: no cover - must not run
        raise AssertionError("planner should not be called on resume")


class StubExecutor:
    kind = "x"

    def __init__(self, cost: float = 0.0, fail: bool = False) -> None:
        self.cost = cost
        self.fail = fail
        self.calls: list[str] = []

    async def execute(self, step: PlanStep, state: RunState) -> StepResult:
        self.calls.append(step.id)
        if self.fail:
            raise RuntimeError("boom")
        return StepResult(
            step_id=step.id,
            kind=step.kind,
            ok=True,
            summary=f"did {step.id}",
            findings=[Finding(claim=f"finding {step.id}", source="analytics", step_id=step.id)],
            cost_usd=self.cost,
        )


class AlwaysSufficient:
    async def reflect(self, goal, findings, results) -> Reflection:  # type: ignore[no-untyped-def]
        return Reflection(sufficient=True)


class NeverSufficient:
    async def reflect(self, goal, findings, results) -> Reflection:  # type: ignore[no-untyped-def]
        return Reflection(sufficient=False, gaps=["need more"], should_replan=True)


def _svc(planner, reflection, executor, **kw) -> OrchestratorService:  # type: ignore[no-untyped-def]
    return OrchestratorService(
        interpreter=StubInterpreter(),
        planner=planner,
        router=Router(),
        reflection=reflection,
        executors={"x": executor},
        checkpointer=kw.pop("checkpointer", InMemoryCheckpointer()),
        **kw,
    )


def _state() -> RunState:
    return RunState(
        run_id=uuid4(), tenant_id=uuid4(), user_id=uuid4(), question="why did EMEA margin fall?"
    )


async def test_happy_path_streams_and_completes() -> None:
    svc = _svc(FixedPlanner(), AlwaysSufficient(), StubExecutor())
    state = _state()
    events = [e async for e in svc.stream(state)]
    types = [e.type for e in events]
    assert state.status == RunStatus.DONE
    assert "plan.created" in types  # plan preview (US-A2)
    assert types.count("step.completed") == 2  # multi-step
    assert "finding.partial" in types  # streamed partial findings (US-A3)
    assert types[-1] == "run.done"
    assert len(state.findings) == 2


async def test_replan_then_sufficient() -> None:
    class OnceInsufficient:
        def __init__(self) -> None:
            self.n = 0

        async def reflect(self, goal, findings, results):  # type: ignore[no-untyped-def]
            self.n += 1
            return Reflection(
                sufficient=self.n >= 2, gaps=["g"] if self.n < 2 else [], should_replan=self.n < 2
            )

    # Planner varies so the re-plan isn't flagged as a loop.
    class GapPlanner:
        def __init__(self) -> None:
            self.calls = 0

        async def plan(self, goal, gaps):  # type: ignore[no-untyped-def]
            self.calls += 1
            steps = [
                PlanStep(id=f"s{self.calls}", kind="x", description=f"unique step {self.calls}")
            ]
            return Plan(steps=steps, rationale="r")

    planner = GapPlanner()
    svc = _svc(planner, OnceInsufficient(), StubExecutor(), loop_similarity=0.99)
    state = _state()
    await svc.run(state)
    assert state.status == RunStatus.DONE
    assert state.iterations == 2  # it re-planned exactly once
    assert planner.calls == 2


async def test_loop_detection_stops_run() -> None:
    svc = _svc(FixedPlanner(), NeverSufficient(), StubExecutor(), loop_similarity=0.9)
    state = _state()
    await svc.run(state)
    assert state.stop_reason == "loop_detected"
    assert state.iterations == 1  # identical re-plan caught immediately


async def test_iteration_cap() -> None:
    class VaryPlanner:
        def __init__(self) -> None:
            self.n = 0

        async def plan(self, goal, gaps):  # type: ignore[no-untyped-def]
            self.n += 1
            return Plan(
                steps=[
                    PlanStep(id=f"s{self.n}", kind="x", description=f"distinct words alpha{self.n}")
                ]
            )

    svc = _svc(
        VaryPlanner(), NeverSufficient(), StubExecutor(), max_iterations=3, loop_similarity=0.999
    )
    state = _state()
    await svc.run(state)
    assert state.stop_reason == "iteration_cap"
    assert state.iterations == 3


async def test_cost_ceiling() -> None:
    svc = _svc(
        FixedPlanner(),
        NeverSufficient(),
        StubExecutor(cost=5.0),
        max_cost_usd=2.5,
        loop_similarity=0.0,
    )
    state = _state()
    await svc.run(state)
    assert state.stop_reason == "cost_ceiling"
    assert state.cost_usd > 2.5


async def test_step_failure_after_retries_marks_failed() -> None:
    executor = StubExecutor(fail=True)
    svc = _svc(
        FixedPlanner(), AlwaysSufficient(), executor, retry_attempts=2, retry_base_delay_s=0.0
    )
    state = _state()
    await svc.run(state)
    # 2 steps x (1 try + 2 retries) = 6 calls; no findings -> FAILED.
    assert len(executor.calls) == 6
    assert state.status == RunStatus.FAILED
    assert state.errors


async def test_resume_skips_completed_steps_and_does_not_replan() -> None:
    executor = StubExecutor()
    svc = _svc(RaisingPlanner(), AlwaysSufficient(), executor)
    # A checkpoint mid-run: goal + plan present, step "a" already done.
    state = RunState(
        run_id=uuid4(),
        tenant_id=uuid4(),
        user_id=uuid4(),
        question="q",
        status=RunStatus.EXECUTING,
        goal=Goal(objective="q", metrics=[]),
        plan=Plan(
            steps=[
                PlanStep(id="a", kind="x", description="a"),
                PlanStep(id="b", kind="x", description="b"),
            ]
        ),
        completed_step_ids=["a"],
        findings=[Finding(claim="from a", source="retrieval", step_id="a")],
    )
    await svc.run(state)
    assert executor.calls == ["b"]  # only the remaining step ran (idempotent resume)
    assert state.status == RunStatus.DONE


async def test_checkpoint_persisted_each_node() -> None:
    cp = InMemoryCheckpointer()
    svc = _svc(FixedPlanner(), AlwaysSufficient(), StubExecutor(), checkpointer=cp)
    state = _state()
    await svc.run(state)
    loaded = await cp.load(state.tenant_id, state.run_id)
    assert loaded is not None
    assert loaded.status == RunStatus.DONE
    assert set(loaded.completed_step_ids) == {"a", "b"}
