"""Governed-autonomy models (Phase 9, FR-034, SEC-07, AP-2).

The safety guarantee: no write or high-impact action runs without explicit human
approval. Before a side-effecting step executes, the engine surfaces a
`ProposedAction` (the exact tool + payload), the `AutonomyPolicy` decides whether
it may auto-run, and if not the run pauses with a pending `ApprovalRequest`. A
human `ApprovalDecision` (approve / reject / edit-then-approve) is applied and the
run resumes — the approval executes under the *approver's* user-scoped identity.

Pure Pydantic/enum — no IO. Shared by the policy, the engine, the approval
service and the gateway.
"""

from __future__ import annotations

from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from eadip.security.rbac import Effect


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class DecisionKind(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    EDIT = "edit"  # amend the payload, then approve the amended action


class ProposedAction(BaseModel):
    """A side-effecting action the engine is about to take, surfaced for review.
    Carries the *exact* payload so the approver sees precisely what will run."""

    step_id: str
    kind: str  # the executor kind, e.g. "tool"
    effect: Effect  # write | high_impact (reads never reach here)
    server: str = ""  # for tool actions
    tool: str = ""
    summary: str = ""  # human-readable one-liner
    payload: dict = Field(default_factory=dict)  # the exact arguments to execute


class ApprovalRequest(BaseModel):
    """A pending gate on a paused run: the proposed action + its lifecycle state.
    Persisted on the RunState (in the checkpoint), so a paused run is durable."""

    id: UUID = Field(default_factory=uuid4)
    action: ProposedAction
    status: ApprovalStatus = ApprovalStatus.PENDING
    requested_at_s: float = 0.0
    decided_by: str = ""  # user id of the approver (audit)
    decided_reason: str = ""
    # The approver's roles — the approved action executes under the approver's
    # authority (user-scoped token), not the original requester's (FR-034).
    approver_roles: tuple[str, ...] = ()
    # When status is APPROVED, this is the payload to actually execute — either the
    # original proposed payload or an operator-edited one (recorded either way).
    approved_payload: dict = Field(default_factory=dict)

    @property
    def is_open(self) -> bool:
        return self.status is ApprovalStatus.PENDING


class ApprovalDecision(BaseModel):
    """A human's ruling on a pending request (US-C1: approve / reject / edit)."""

    kind: DecisionKind
    reason: str = ""
    edited_payload: dict | None = None  # required for EDIT — the amended arguments
