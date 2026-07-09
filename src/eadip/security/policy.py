"""Policy Decision Point (PDP): deny-by-default authorization (AP-5, SEC-03).

Pure logic — no IO. Callers (the gateway authz dependency) are responsible for
recording the decision to the audit log.
"""

from __future__ import annotations

from dataclasses import dataclass

from eadip.security.identity import Identity
from eadip.security.rbac import Effect, RoleCatalog


@dataclass(frozen=True)
class PolicyDecision:
    permit: bool
    reason: str


class PolicyDecisionPoint:
    def __init__(self, catalog: RoleCatalog) -> None:
        self._catalog = catalog

    @property
    def catalog(self) -> RoleCatalog:
        """The live catalog (the admin portal reads and extends it, FR-057)."""
        return self._catalog

    def decide(
        self, identity: Identity, *, resource: str, domain: str, effect: Effect
    ) -> PolicyDecision:
        perms = self._catalog.effective_permissions(identity.roles)
        for perm in perms:
            if perm.matches(resource=resource, domain=domain, effect=effect):
                return PolicyDecision(
                    permit=True,
                    reason=f"granted by {perm.resource}/{perm.domain}/{perm.effect}",
                )
        return PolicyDecision(
            permit=False,
            reason=f"no permission for {resource}/{domain}/{effect} (deny-by-default)",
        )
