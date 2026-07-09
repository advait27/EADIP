"""Authorization dependency: enforce + audit at the boundary (AP-5, SEC-03/08).

`require(resource, domain, effect)` returns a FastAPI dependency that asks the
PDP, records the decision to the audit log, and raises 403 on deny. Every
protected route both enforces and leaves an audit trail.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import HTTPException

from eadip.gateway.dependencies import AuditLogDep, IdentityDep, PdpDep
from eadip.security.audit import make_event
from eadip.security.identity import Identity
from eadip.security.rbac import Effect


def require(resource: str, domain: str, effect: Effect) -> Callable[..., Awaitable[Identity]]:
    async def _authorize(identity: IdentityDep, pdp: PdpDep, audit: AuditLogDep) -> Identity:
        decision = pdp.decide(identity, resource=resource, domain=domain, effect=effect)
        await audit.record(
            make_event(
                tenant_id=identity.tenant_id,
                actor=str(identity.user_id),
                action="authz.decision",
                detail={
                    "resource": resource,
                    "domain": domain,
                    "effect": str(effect),
                    "permit": decision.permit,
                    "reason": decision.reason,
                },
            )
        )
        if not decision.permit:
            raise HTTPException(status_code=403, detail="forbidden")
        return identity

    return _authorize
