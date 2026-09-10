"""Governed autonomy in the engine (Phase 9, interrupt_before): a side-effecting
step pauses the run, and only an approval resumes + executes it. Reject skips it.
Deterministic via a preflight-capable stub executor."""

from __future__ import annotations

from uuid import uuid4

from eadip.agents.router import Router
from eadip.approval.models import (
    ApprovalDecision,
    ApprovalStatus,
    DecisionKind,
    ProposedAction,
)
from eadip.approval.policy import AutonomyPolicy
from eadip.approval.service import ApprovalService
from eadip.domain.entities import RunStatus
from eadip.orchestrator.checkpoint import InMemoryCheckpointer
from eadip.orchestrator.models import Finding, Goal, Plan, PlanStep, Reflection, StepResult
from eadip.orchestrator.service import OrchestratorService
from eadip.orchestrator.state import RunState
from eadip.security.rbac import Effect


class StubInterpreter:
    async def interpret(self, question: str, *, tenant_id: object = None) -> Goal:
        return Goal(objective=question, metrics=[])


class OnePlanPlanner:
    """A single read step then a write step that depends on it."""

    async def plan(self, goal: Goal, gaps: list[str]) -> Plan:
        return Plan(
            steps=[
                PlanStep(id="read", kind="read", description="gather"),
                PlanStep(id="write", kind="tool", description="act", depends_on=["read"]),
            ],
            rationale="r",
        )


class AlwaysSufficient:
    async def reflect(self, goal, findings, results) -> Reflection:  # type: ignore[no-untyped-def]
        return Reflection(sufficient=True)


class ReadExecutor:
    kind = "read"

    async def execute(self, step: PlanStep, state: RunState) -> StepResult:
        return StepResult(
            step_id=step.id,
            kind=step.kind,
            ok=True,
            findings=[Finding(claim="grounded", source="retrieval", step_id=step.id)],
        )


class WriteToolExecutor:
    """A preflight-capable executor: its step is a WRITE, so it must be gated."""

    kind = "tool"

    def __init__(self) -> None:
        self.executed: list[dict] = []

    async def preflight(self, step: PlanStep, state: RunState) -> ProposedAction:
        return ProposedAction(
            step_id=step.id,
            kind=self.kind,
            effect=Effect.WRITE,
            server="svc",
            tool="open_ticket",
            summary="call svc.open_ticket (write)",
            payload=dict(step.params.get("arguments", {})),
        )

    async def execute(self, step: PlanStep, state: RunState) -> StepResult:
        self.executed.append(dict(step.params.get("arguments", {})))
        return StepResult(
            step_id=step.id,
            kind=self.kind,
            ok=True,
            findings=[Finding(claim="ticket opened", source="tool", step_id=step.id)],
        )


def _svc(write_exec: WriteToolExecutor, cp: InMemoryCheckpointer) -> OrchestratorService:
    return OrchestratorService(
        interpreter=StubInterpreter(),
        planner=OnePlanPlanner(),
        router=Router(),
        reflection=AlwaysSufficient(),
        executors={"read": ReadExecutor(), "tool": write_exec},
        checkpointer=cp,
        autonomy=AutonomyPolicy.safe_default(),
    )


def _state() -> RunState:
    return RunState(run_id=uuid4(), tenant_id=uuid4(), user_id=uuid4(), question="q")


async def test_write_step_pauses_for_approval() -> None:
    write_exec = WriteToolExecutor()
    cp = InMemoryCheckpointer()
    svc = _svc(write_exec, cp)
    state = _state()
    events = [e async for e in svc.stream(state)]
    types = [e.type for e in events]

    assert "approval.required" in types
    assert state.status is RunStatus.AWAITING_APPROVAL
    assert not write_exec.executed  # the write did NOT run (0 unapproved actions)
    assert len(state.open_approvals()) == 1
    # the read step still ran and its finding is present.
    assert any(f.step_id == "read" for f in state.findings)


async def test_approve_resumes_and_executes_the_write() -> None:
    write_exec = WriteToolExecutor()
    cp = InMemoryCheckpointer()
    svc = _svc(write_exec, cp)
    state = _state()
    await svc.run(state)
    approval = state.open_approvals()[0]

    await ApprovalService(cp).decide(
        state.tenant_id,
        state.run_id,
        approval.id,
        ApprovalDecision(kind=DecisionKind.APPROVE),
        actor="approver",
    )
    resumed = await cp.load(state.tenant_id, state.run_id)
    assert resumed is not None
    await svc.run(resumed)

    assert len(write_exec.executed) == 1  # executed exactly once, after approval
    assert resumed.status is RunStatus.DONE


async def test_reject_skips_the_write_and_completes() -> None:
    write_exec = WriteToolExecutor()
    cp = InMemoryCheckpointer()
    svc = _svc(write_exec, cp)
    state = _state()
    await svc.run(state)
    approval = state.open_approvals()[0]

    await ApprovalService(cp).decide(
        state.tenant_id,
        state.run_id,
        approval.id,
        ApprovalDecision(kind=DecisionKind.REJECT, reason="no"),
        actor="approver",
    )
    resumed = await cp.load(state.tenant_id, state.run_id)
    assert resumed is not None
    await svc.run(resumed)

    assert not write_exec.executed  # never executed
    assert "write" in resumed.rejected_step_ids
    assert resumed.status is RunStatus.DONE


async def test_edit_then_approve_executes_amended_payload() -> None:
    write_exec = WriteToolExecutor()
    cp = InMemoryCheckpointer()
    svc = _svc(write_exec, cp)
    state = _state()
    # seed an argument on the write step via a custom plan.
    await svc.run(state)
    approval = state.open_approvals()[0]

    await ApprovalService(cp).decide(
        state.tenant_id,
        state.run_id,
        approval.id,
        ApprovalDecision(kind=DecisionKind.EDIT, edited_payload={"title": "amended"}),
        actor="approver",
    )
    resumed = await cp.load(state.tenant_id, state.run_id)
    assert resumed is not None
    await svc.run(resumed)

    assert write_exec.executed == [{"title": "amended"}]  # the edited payload ran


async def test_double_decide_is_refused() -> None:
    write_exec = WriteToolExecutor()
    cp = InMemoryCheckpointer()
    svc = _svc(write_exec, cp)
    state = _state()
    await svc.run(state)
    approval = state.open_approvals()[0]
    service = ApprovalService(cp)
    await service.decide(
        state.tenant_id,
        state.run_id,
        approval.id,
        ApprovalDecision(kind=DecisionKind.APPROVE),
        actor="a",
    )
    # a second ruling on the same request is refused (no double-execute).
    import pytest

    from eadip.approval.service import ApprovalError

    with pytest.raises(ApprovalError):
        await service.decide(
            state.tenant_id,
            state.run_id,
            approval.id,
            ApprovalDecision(kind=DecisionKind.REJECT),
            actor="a",
        )
    reloaded = await cp.load(state.tenant_id, state.run_id)
    assert reloaded is not None
    assert reloaded.approvals[0].status is ApprovalStatus.APPROVED
