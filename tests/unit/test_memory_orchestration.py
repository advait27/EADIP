"""Engine + memory (Phase 11, FR-045): a finished run is consolidated, and a
repeat question reuses the prior plan (plan.reused) without calling the planner —
while execution, verification and governance still run normally."""

from __future__ import annotations

from uuid import uuid4

from eadip.agents.router import Router
from eadip.domain.entities import RunStatus
from eadip.memory.agent import MemoryAgent
from eadip.memory.ports import InMemoryEpisodicStore, InMemorySemanticStore
from eadip.orchestrator.checkpoint import InMemoryCheckpointer
from eadip.orchestrator.models import Finding, Goal, Plan, PlanStep, Reflection, StepResult
from eadip.orchestrator.service import OrchestratorService
from eadip.orchestrator.state import RunState


class StubInterpreter:
    async def interpret(self, question: str) -> Goal:
        return Goal(objective=question, metrics=["margin"], complexity="deep")


class CountingPlanner:
    def __init__(self) -> None:
        self.calls = 0

    async def plan(self, goal: Goal, gaps: list[str]) -> Plan:
        self.calls += 1
        return Plan(
            steps=[PlanStep(id="a", kind="x", description="step a")],
            rationale="fresh plan",
        )


class StubExecutor:
    kind = "x"

    async def execute(self, step: PlanStep, state: RunState) -> StepResult:
        return StepResult(
            step_id=step.id,
            kind=step.kind,
            ok=True,
            summary=f"did {step.id}",
            findings=[Finding(claim=f"finding {step.id}", source="analytics", step_id=step.id)],
        )


class AlwaysSufficient:
    async def reflect(self, goal, findings, results) -> Reflection:  # type: ignore[no-untyped-def]
        return Reflection(sufficient=True)


def _svc(planner: CountingPlanner, memory: MemoryAgent | None) -> OrchestratorService:
    return OrchestratorService(
        interpreter=StubInterpreter(),
        planner=planner,
        router=Router(),
        reflection=AlwaysSufficient(),
        executors={"x": StubExecutor()},
        checkpointer=InMemoryCheckpointer(),
        memory=memory,
    )


def _state(tenant_id, question="why did EMEA margin fall?"):  # type: ignore[no-untyped-def]
    return RunState(run_id=uuid4(), tenant_id=tenant_id, user_id=uuid4(), question=question)


async def test_repeat_question_reuses_prior_plan() -> None:
    memory = MemoryAgent(episodic=InMemoryEpisodicStore(), semantic=InMemorySemanticStore())
    planner = CountingPlanner()
    svc = _svc(planner, memory)
    tenant = uuid4()

    first_events = [e async for e in svc.stream(_state(tenant))]
    assert "memory.consolidated" in [e.type for e in first_events]
    assert planner.calls == 1

    # Same ask, different phrasing: the plan comes from episodic memory.
    second = _state(tenant, "WHY did the EMEA margin fall??")
    second_events = [e async for e in svc.stream(second)]
    types = [e.type for e in second_events]
    assert "plan.reused" in types and "plan.created" not in types
    assert planner.calls == 1  # planner was not consulted again
    assert second.status is RunStatus.DONE
    assert types.count("step.completed") == 1  # the reused plan still executed


async def test_reuse_is_tenant_scoped_and_off_without_memory() -> None:
    memory = MemoryAgent(episodic=InMemoryEpisodicStore(), semantic=InMemorySemanticStore())
    planner = CountingPlanner()
    svc = _svc(planner, memory)
    [_ async for _ in svc.stream(_state(uuid4()))]

    # Another tenant asking the same question must not see the plan (isolation).
    other_events = [e.type async for e in svc.stream(_state(uuid4()))]
    assert "plan.created" in other_events and "plan.reused" not in other_events

    # memory=None: engine behaves exactly as it did through Phase 10.
    svc_off = _svc(CountingPlanner(), None)
    off_events = [e.type async for e in svc_off.stream(_state(uuid4()))]
    assert "plan.created" in off_events
    assert "memory.consolidated" not in off_events
