"""RunState: the durable, checkpointed state of one investigation (TAD Ch 6).

Pydantic so it serialises straight into the checkpoint and over SSE. The
``apply_step_results`` reducer is what makes parallel branches safe: each branch
returns a ``StepResult`` independently and they are merged deterministically
(ordered by step id), never racing on shared state.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field

from eadip.approval.models import ApprovalRequest, ApprovalStatus
from eadip.domain.entities import RunStatus
from eadip.orchestrator.models import Finding, Goal, Plan, Reflection, StepResult
from eadip.verification.models import ExecutiveBrief, VerifiedClaim


class RunState(BaseModel):
    run_id: UUID
    tenant_id: UUID
    user_id: UUID
    question: str
    acl_tags: tuple[str, ...] = ()

    status: RunStatus = RunStatus.QUEUED
    goal: Goal | None = None
    plan: Plan | None = None

    completed_step_ids: list[str] = Field(default_factory=list)
    step_results: list[StepResult] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    reflections: list[Reflection] = Field(default_factory=list)
    plan_history: list[str] = Field(default_factory=list)  # signatures, for loop detection

    iterations: int = 0
    cost_usd: float = 0.0
    elapsed_s: float = 0.0
    errors: list[str] = Field(default_factory=list)
    stop_reason: str | None = None  # set when a bound or loop terminated the run

    # Phase 7 — verified output (durable in the checkpoint, i.e. run memory).
    verified_claims: list[VerifiedClaim] = Field(default_factory=list)
    brief: ExecutiveBrief | None = None

    # Phase 9 — governed autonomy. The approval ledger (durable in the checkpoint):
    # every gated action ever surfaced for this run, with its decision.
    approvals: list[ApprovalRequest] = Field(default_factory=list)
    # Step ids the human rejected — the engine skips them and records the skip.
    rejected_step_ids: list[str] = Field(default_factory=list)

    def open_approvals(self) -> list[ApprovalRequest]:
        return [a for a in self.approvals if a.status is ApprovalStatus.PENDING]

    def approval_for_step(self, step_id: str) -> ApprovalRequest | None:
        """The most recent approval request raised for a step (if any)."""
        for a in reversed(self.approvals):
            if a.action.step_id == step_id:
                return a
        return None

    def apply_step_results(self, results: list[StepResult]) -> None:
        """Reducer: merge a (possibly parallel) batch of step results in a
        deterministic order, so concurrent branches never race the state."""
        for r in sorted(results, key=lambda x: x.step_id):
            self.step_results.append(r)
            if r.step_id not in self.completed_step_ids:
                self.completed_step_ids.append(r.step_id)
            self.findings.extend(r.findings)
            self.cost_usd += r.cost_usd
            if r.error:
                self.errors.append(f"{r.step_id}: {r.error}")

    def pending_steps(self) -> list[str]:
        if self.plan is None:
            return []
        return [s.id for s in self.plan.steps if s.id not in self.completed_step_ids]

    def is_resumable(self) -> bool:
        """A checkpoint is a resume (not a fresh start) once interpretation ran."""
        return self.goal is not None and self.status not in (
            RunStatus.DONE,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        )
