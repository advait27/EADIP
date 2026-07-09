"""Run lifecycle routes: accept an investigation + stream orchestration (FR-001,
FR-005). The events stream IS the orchestrator running — it interprets, plans,
routes, executes and reflects, emitting plan-preview + step + partial-finding
events over SSE, and checkpointing as it goes (so a dropped connection resumes).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sse_starlette.sse import EventSourceResponse

from eadip.approval.models import ApprovalDecision, DecisionKind
from eadip.approval.service import ApprovalError
from eadip.domain.entities import Run
from eadip.gateway.authz import require
from eadip.gateway.dependencies import (
    ApprovalServiceDep,
    AuditLogDep,
    BudgetLedgerDep,
    CheckpointerDep,
    ConcurrencyGateDep,
    NotificationServiceDep,
    OrchestratorDep,
    ResidencyGuardDep,
    RunRepositoryDep,
)
from eadip.gateway.models import (
    ApprovalItem,
    CreateRun,
    DecisionRequest,
    DecisionResponse,
    ReportResponse,
    RunResponse,
)
from eadip.observability.logging import get_logger
from eadip.orchestrator.state import RunState
from eadip.platform.models import Notification, NotificationKind
from eadip.platform.notifications import notifications_from_event
from eadip.security.audit import make_event
from eadip.security.identity import Identity
from eadip.security.rbac import Effect
from eadip.verification.models import VerificationStatus

router = APIRouter(prefix="/v1/runs", tags=["runs"])
log = get_logger(__name__)

# Running an investigation is a read-only action on the "runs" domain.
RunActorDep = Annotated[Identity, Depends(require("run", "runs", Effect.READ))]
# Authorizing a side-effecting action is a write on the tool surface (approver):
# the same grant that gates the action itself gates who may approve it (Phase 9).
ApproverDep = Annotated[Identity, Depends(require("tool", "*", Effect.WRITE))]


@router.post("", status_code=status.HTTP_202_ACCEPTED, response_model=RunResponse)
async def create_run(
    body: CreateRun,
    request: Request,
    identity: RunActorDep,
    _residency: ResidencyGuardDep,
    repo: RunRepositoryDep,
    checkpointer: CheckpointerDep,
    audit: AuditLogDep,
    budgets: BudgetLedgerDep,
) -> RunResponse:
    """Accept an investigation (FR-001): persist the run + an initial checkpoint,
    then hand back the SSE URL that drives the orchestration."""
    # Per-tenant budget (Phase 11, FR-053): an exhausted monthly cap refuses NEW
    # runs; in-flight runs stay bounded by the per-run cost ceiling.
    if not await budgets.allows_new_run(identity.tenant_id):
        raise HTTPException(status_code=429, detail="tenant budget exhausted")
    run = Run(tenant_id=identity.tenant_id, user_id=identity.user_id, question=body.question)
    await repo.add(run)
    state = RunState(
        run_id=run.id,
        tenant_id=identity.tenant_id,
        user_id=identity.user_id,
        question=body.question,
        acl_tags=identity.roles,  # Phase 4/6 bridge: roles double as ACL tags
    )
    await checkpointer.save(state)
    await audit.record(
        make_event(
            tenant_id=identity.tenant_id,
            actor=str(identity.user_id),
            action="run.create",
            run_id=run.id,
            detail={"question_len": len(body.question)},
        )
    )
    log.info("run.created", run_id=str(run.id), tenant_id=str(identity.tenant_id))
    events_url = str(request.url_for("stream_run_events", run_id=run.id))
    return RunResponse(id=run.id, status=run.status, question=run.question, events_url=events_url)


@router.get("/{run_id}/events", name="stream_run_events")
async def stream_run_events(
    run_id: UUID,
    identity: RunActorDep,
    _residency: ResidencyGuardDep,
    checkpointer: CheckpointerDep,
    orchestrator: OrchestratorDep,
    audit: AuditLogDep,
    gate: ConcurrencyGateDep,
    notifications: NotificationServiceDep,
    budgets: BudgetLedgerDep,
) -> EventSourceResponse:
    """Stream the orchestration over SSE (FR-005, NFR-01). Loads the run's
    checkpoint (tenant-scoped) and runs the engine, relaying its events; a fresh
    run starts from interpret, a checkpointed one resumes at the last step.

    Phase 11 taps the stream it is already relaying: notifications fan out on
    approval/completion/anomaly events, the run's final cost is charged to the
    tenant budget, and concurrent streams per tenant are bounded (backpressure)."""
    state = await checkpointer.load(identity.tenant_id, run_id)
    if state is None:
        raise HTTPException(status_code=404, detail="run not found")
    gate_key = str(identity.tenant_id)
    if not gate.try_acquire(gate_key):
        raise HTTPException(
            status_code=429,
            detail="too many concurrent runs for this tenant",
            headers={"Retry-After": "5"},
        )
    await audit.record(
        make_event(
            tenant_id=identity.tenant_id,
            actor=str(identity.user_id),
            action="run.stream",
            run_id=run_id,
        )
    )

    async def event_generator() -> AsyncIterator[dict[str, str]]:
        try:
            async for event in orchestrator.stream(state):
                for note in notifications_from_event(identity.tenant_id, run_id, event):
                    await notifications.publish(note)
                if event.type == "run.done":
                    budget = await budgets.charge(
                        identity.tenant_id, float(event.data.get("cost_usd", 0.0))
                    )
                    if budget.exhausted:
                        await notifications.publish(
                            Notification(
                                tenant_id=identity.tenant_id,
                                kind=NotificationKind.BUDGET_EXHAUSTED,
                                severity="warning",
                                title="Monthly budget exhausted — new runs will be refused",
                                body=f"spent ${budget.spent_usd:g} of ${budget.monthly_cap_usd:g}",
                                run_id=run_id,
                            )
                        )
                yield {"event": event.type, "data": json.dumps(event.data)}
        finally:
            gate.release(gate_key)

    return EventSourceResponse(event_generator())


@router.get("/{run_id}/report", name="run_report", response_model=ReportResponse)
async def run_report(
    run_id: UUID,
    identity: RunActorDep,
    checkpointer: CheckpointerDep,
    audit: AuditLogDep,
) -> ReportResponse:
    """The verified executive brief for a completed run (EXP-01/02): headline,
    key findings with confidence + provenance + verification status,
    recommendations, assumptions/limitations, and the raw drill-down."""
    state = await checkpointer.load(identity.tenant_id, run_id)
    if state is None:
        raise HTTPException(status_code=404, detail="run not found")
    if state.brief is None:
        raise HTTPException(status_code=409, detail="run has no verified brief yet")
    await audit.record(
        make_event(
            tenant_id=identity.tenant_id,
            actor=str(identity.user_id),
            action="run.report",
            run_id=run_id,
        )
    )
    claims = state.verified_claims
    return ReportResponse(
        run_id=run_id,
        status=str(state.status),
        verified=sum(c.status == VerificationStatus.VERIFIED for c in claims),
        unverified=sum(c.status == VerificationStatus.UNVERIFIED for c in claims),
        conflicting=sum(c.status == VerificationStatus.CONFLICTING for c in claims),
        brief=state.brief.model_dump(mode="json"),
    )


@router.get("/{run_id}/approvals", name="list_approvals", response_model=list[ApprovalItem])
async def list_approvals(
    run_id: UUID,
    identity: RunActorDep,
    approvals: ApprovalServiceDep,
    audit: AuditLogDep,
) -> list[ApprovalItem]:
    """Pending approvals for a paused run (US-C1): the exact proposed actions +
    payloads awaiting a human ruling. A reader may see what is pending."""
    try:
        pending = await approvals.list_pending(identity.tenant_id, run_id)
    except ApprovalError:
        raise HTTPException(status_code=404, detail="run not found") from None
    await audit.record(
        make_event(
            tenant_id=identity.tenant_id,
            actor=str(identity.user_id),
            action="approval.list",
            run_id=run_id,
            detail={"pending": len(pending)},
        )
    )
    return [
        ApprovalItem(
            id=a.id,
            status=str(a.status),
            step_id=a.action.step_id,
            kind=a.action.kind,
            effect=str(a.action.effect),
            server=a.action.server,
            tool=a.action.tool,
            summary=a.action.summary,
            payload=a.action.payload,
        )
        for a in pending
    ]


@router.post(
    "/{run_id}/approvals/{approval_id}", name="decide_approval", response_model=DecisionResponse
)
async def decide_approval(
    run_id: UUID,
    approval_id: UUID,
    body: DecisionRequest,
    request: Request,
    identity: ApproverDep,
    approvals: ApprovalServiceDep,
    audit: AuditLogDep,
) -> DecisionResponse:
    """Rule on a pending approval (US-C1): approve / reject / edit-then-approve.
    Requires the approver grant — the same authority that gates the action. On
    approve, re-open the returned SSE URL to resume the run; on reject the step is
    recorded and skipped. Every decision is audited."""
    decision = ApprovalDecision(
        kind=DecisionKind(body.decision),
        reason=body.reason,
        edited_payload=body.edited_payload,
    )
    try:
        result = await approvals.decide(
            identity.tenant_id,
            run_id,
            approval_id,
            decision,
            actor=str(identity.user_id),
            actor_roles=identity.roles,
        )
    except ApprovalError as exc:
        # not-found vs already-decided both surface as safe 4xx (no double-execute).
        code = 404 if "not found" in str(exc) else 409
        raise HTTPException(status_code=code, detail=str(exc)) from None

    req = result.request
    await audit.record(
        make_event(
            tenant_id=identity.tenant_id,
            actor=str(identity.user_id),
            action="approval.decision",
            run_id=run_id,
            detail={
                "approval_id": str(req.id),
                "step_id": req.action.step_id,
                "tool": req.action.tool,
                "effect": str(req.action.effect),
                "decision": body.decision,
                "status": str(req.status),
            },
        )
    )
    log.info(
        "approval.decision",
        run_id=str(run_id),
        decision=body.decision,
        status=str(req.status),
    )
    return DecisionResponse(
        approval_id=req.id,
        status=str(req.status),
        step_id=req.action.step_id,
        decided_by=req.decided_by,
        resume_url=str(request.url_for("stream_run_events", run_id=run_id)),
    )
