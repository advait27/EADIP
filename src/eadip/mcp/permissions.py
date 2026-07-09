"""Pre-invocation authorization for tools (FR-032, SEC-03/06, AP-5).

Before *any* tool runs, the caller's identity is checked against the tool's
declarative permission descriptor through the same PDP that guards the gateway
(deny-by-default). A tool acting on domain D with effect E is permitted only if
the caller holds a `tool`/D/E grant (see the RBAC catalog's `approver` role for
write/high-impact) and holds every ACL tag the tool requires. This is enforced
*inside* the executor too, not only at the HTTP edge, because the orchestrator
selects and calls tools autonomously (AP-2 lays the ground for Phase 9 approval).

SEC-06: a tool's *output* is never trusted as instructions — `wrap_untrusted`
marks it as data so nothing downstream treats a connector's response as a command.
"""

from __future__ import annotations

from typing import Any

from eadip.mcp.models import ToolSpec
from eadip.security.identity import Identity
from eadip.security.policy import PolicyDecision, PolicyDecisionPoint


def authorize_tool(pdp: PolicyDecisionPoint, identity: Identity, spec: ToolSpec) -> PolicyDecision:
    """Deny-by-default decision for calling `spec` as `identity`. Checks each data
    domain the tool declares (all must be granted) and every required ACL tag."""
    perm = spec.permission

    missing_tags = [t for t in perm.acl_tags if t not in set(identity.roles)]
    if missing_tags:
        return PolicyDecision(
            permit=False,
            reason=f"missing acl tag(s): {','.join(missing_tags)} (deny-by-default)",
        )

    for domain in perm.domains or ("*",):
        decision = pdp.decide(identity, resource="tool", domain=domain, effect=perm.effect)
        if not decision.permit:
            return decision  # first denied domain wins — deny-by-default
    return PolicyDecision(permit=True, reason=f"granted tool/{perm.effect} on {perm.domains}")


def wrap_untrusted(output: Any) -> dict[str, Any]:
    """SEC-06: envelope tool output so downstream consumers treat it strictly as
    data, never as instructions to follow."""
    return {"untrusted": True, "source": "tool_output", "data": output}
