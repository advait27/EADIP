"""Approval service (Phase 9, FR-034): apply a human decision to a paused run.

Loads the run's checkpoint, finds the pending `ApprovalRequest`, and applies the
decision (approve / reject / edit-then-approve), recording who decided and why on
the durable ledger. Approve stamps the exact payload to execute (original or
operator-edited); reject marks the step to be skipped. It does NOT run the
orchestrator — the caller re-opens the SSE stream to resume from the checkpoint,
so execution always flows through the engine's normal governed path.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from eadip.approval.models import (
    ApprovalDecision,
    ApprovalRequest,
    ApprovalStatus,
    DecisionKind,
)
from eadip.orchestrator.checkpoint import Checkpointer
from eadip.orchestrator.state import RunState


class ApprovalError(Exception):
    """A decision could not be applied (unknown run/approval, or already decided)."""


@dataclass(frozen=True)
class ApplyResult:
    state: RunState
    request: ApprovalRequest


class ApprovalService:
    def __init__(self, checkpointer: Checkpointer) -> None:
        self._cp = checkpointer

    async def list_pending(self, tenant_id: UUID, run_id: UUID) -> list[ApprovalRequest]:
        state = await self._load(tenant_id, run_id)
        return state.open_approvals()

    async def decide(
        self,
        tenant_id: UUID,
        run_id: UUID,
        approval_id: UUID,
        decision: ApprovalDecision,
        *,
        actor: str,
        actor_roles: tuple[str, ...] = (),
    ) -> ApplyResult:
        """Apply a ruling to a specific pending approval and persist the run.

        Idempotency/safety: a request that is already approved/rejected cannot be
        re-decided (raises), so a double-submit never double-executes an action.
        """
        state = await self._load(tenant_id, run_id)
        request = next((a for a in state.approvals if a.id == approval_id), None)
        if request is None:
            raise ApprovalError("approval request not found")
        if request.status is not ApprovalStatus.PENDING:
            raise ApprovalError(f"approval already {request.status}")

        if decision.kind is DecisionKind.REJECT:
            request.status = ApprovalStatus.REJECTED
            request.decided_by = actor
            request.decided_reason = decision.reason
            if request.action.step_id not in state.rejected_step_ids:
                state.rejected_step_ids.append(request.action.step_id)
        else:  # APPROVE or EDIT-then-approve
            payload = dict(request.action.payload)
            if decision.kind is DecisionKind.EDIT:
                if decision.edited_payload is None:
                    raise ApprovalError("edit decision requires an edited_payload")
                payload = dict(decision.edited_payload)
            request.status = ApprovalStatus.APPROVED
            request.decided_by = actor
            request.decided_reason = decision.reason
            request.approver_roles = tuple(actor_roles)  # execute under the approver's authority
            request.approved_payload = payload

        await self._cp.save(state)
        return ApplyResult(state=state, request=request)

    async def _load(self, tenant_id: UUID, run_id: UUID) -> RunState:
        state = await self._cp.load(tenant_id, run_id)
        if state is None:
            raise ApprovalError("run not found")
        return state
