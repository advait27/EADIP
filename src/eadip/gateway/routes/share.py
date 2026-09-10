"""Share links (Glass Box): one link replays an investigation, no login.

``POST /v1/runs/{id}/share`` (an analyst on the run's tenant) mints a signed,
scoped, expiring token. ``GET /v1/share/{token}`` takes no identity: the token
IS the authorization — read-only, one run, one purpose (``scope: replay``).
A plain service JWT signed with the same key is refused (scope check). The
residency rule still applies for the tenant named in the token, and every
view is audited under ``actor="share:<jti>"``. Expired or malformed tokens are
indistinguishable from unknown ones (404). Revocation before expiry is a
follow-up (a jti deny-list).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID, uuid4

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request

from eadip.gateway.authz import require
from eadip.gateway.dependencies import (
    AuditLogDep,
    CheckpointerDep,
    EventLogDep,
    ResidencyGuardDep,
    ResidencyPolicyDep,
    SettingsDep,
    TokenIssuerDep,
    WarehouseDep,
)
from eadip.gateway.models import SharePayload, ShareResponse
from eadip.observability.logging import get_logger
from eadip.orchestrator.graph import build_evidence_graph
from eadip.orchestrator.state import RunState
from eadip.security.audit import make_event
from eadip.security.identity import Identity
from eadip.security.rbac import Effect
from eadip.verification.bundle import EvidenceBundle, build_evidence_bundle

router = APIRouter(tags=["share"])
log = get_logger(__name__)

SHARE_SCOPE = "replay"
RunActorDep = Annotated[Identity, Depends(require("run", "runs", Effect.READ))]


def _ui_available(request: Request) -> bool:
    return bool(getattr(request.app.state, "ui_mounted", False))


@router.post("/v1/runs/{run_id}/share", name="share_run", response_model=ShareResponse)
async def share_run(
    run_id: UUID,
    request: Request,
    identity: RunActorDep,
    _residency: ResidencyGuardDep,
    checkpointer: CheckpointerDep,
    issuer: TokenIssuerDep,
    settings: SettingsDep,
    audit: AuditLogDep,
) -> ShareResponse:
    state = await checkpointer.load(identity.tenant_id, run_id)
    if state is None:
        raise HTTPException(status_code=404, detail="run not found")
    jti = uuid4().hex
    token = issuer.mint_claims(
        {
            "scope": SHARE_SCOPE,
            "run": str(run_id),
            "tenant": str(identity.tenant_id),
            "jti": jti,
            "by": str(identity.user_id),
        },
        ttl_s=settings.share_link_ttl_s,
    )
    expires_at = datetime.fromtimestamp(issuer.verify(token)["exp"], tz=UTC)
    api_url = str(request.url_for("share_view", token=token))
    url = (
        str(request.base_url).rstrip("/") + f"/app/share/{token}"
        if _ui_available(request)
        else api_url
    )
    await audit.record(
        make_event(
            tenant_id=identity.tenant_id,
            actor=str(identity.user_id),
            action="run.share.create",
            run_id=run_id,
            detail={"jti": jti, "expires_at": expires_at.isoformat()},
        )
    )
    log.info("run.shared", run_id=str(run_id), jti=jti)
    return ShareResponse(token=token, url=url, api_url=api_url, expires_at=expires_at)


async def _resolve_share(
    token: str,
    issuer: TokenIssuerDep,
    residency: ResidencyPolicyDep,
    checkpointer: CheckpointerDep,
) -> tuple[dict[str, Any], RunState]:
    try:
        claims = issuer.verify(token)
    except jwt.PyJWTError:
        raise HTTPException(status_code=404, detail="share link not found") from None
    if claims.get("scope") != SHARE_SCOPE or "run" not in claims or "tenant" not in claims:
        raise HTTPException(status_code=404, detail="share link not found")
    try:
        run_id, tenant_id = UUID(str(claims["run"])), UUID(str(claims["tenant"]))
    except ValueError:
        raise HTTPException(status_code=404, detail="share link not found") from None
    if not await residency.allows(tenant_id):
        pinned = await residency.region_for(tenant_id)
        raise HTTPException(
            status_code=451,
            detail=f"tenant data resides in region '{pinned}'; "
            f"this deployment serves '{residency.deployment_region}'",
        )
    state = await checkpointer.load(tenant_id, run_id)
    if state is None:
        raise HTTPException(status_code=404, detail="share link not found")
    return claims, state


@router.get("/v1/share/{token}", name="share_view", response_model=SharePayload)
async def share_view(
    token: str,
    request: Request,
    issuer: TokenIssuerDep,
    residency: ResidencyPolicyDep,
    checkpointer: CheckpointerDep,
    event_log: EventLogDep,
    audit: AuditLogDep,
) -> SharePayload:
    claims, state = await _resolve_share(token, issuer, residency, checkpointer)
    await audit.record(
        make_event(
            tenant_id=state.tenant_id,
            actor=f"share:{claims.get('jti', '')}",
            action="run.share.view",
            run_id=state.run_id,
        )
    )
    events = await event_log.read(state.run_id)
    return SharePayload(
        run_id=state.run_id,
        question=state.question,
        status=str(state.status),
        expires_at=datetime.fromtimestamp(int(claims["exp"]), tz=UTC),
        timeline=[e.model_dump(mode="json") for e in events],
        graph=build_evidence_graph(state).model_dump(mode="json"),
        brief=state.brief.model_dump(mode="json") if state.brief is not None else None,
        evidence_bundle_url=str(request.url_for("share_evidence_bundle", token=token)),
    )


@router.get(
    "/v1/share/{token}/evidence-bundle",
    name="share_evidence_bundle",
    response_model=EvidenceBundle,
)
async def share_evidence_bundle(
    token: str,
    issuer: TokenIssuerDep,
    residency: ResidencyPolicyDep,
    checkpointer: CheckpointerDep,
    warehouse: WarehouseDep,
    settings: SettingsDep,
    audit: AuditLogDep,
) -> EvidenceBundle:
    """Served separately from the replay payload: it is the heavy part (up to the
    row cap per table) and only the verify panel needs it."""
    claims, state = await _resolve_share(token, issuer, residency, checkpointer)
    bundle = await build_evidence_bundle(
        state,
        warehouse,
        row_cap=settings.analytics_row_cap,
        rel_tolerance=settings.verification_rel_tolerance,
        timeout_s=settings.analytics_statement_timeout_s,
        max_join_tables=settings.analytics_max_join_tables,
    )
    await audit.record(
        make_event(
            tenant_id=state.tenant_id,
            actor=f"share:{claims.get('jti', '')}",
            action="run.share.evidence_bundle",
            run_id=state.run_id,
            detail={"claims": len(bundle.claims)},
        )
    )
    return bundle
