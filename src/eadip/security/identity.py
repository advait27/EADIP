"""The authenticated caller.

Phase 1 populates this from a dev stub (see eadip.gateway.auth). Phase 2 wires
OIDC/SAML + RBAC so identity comes from verified IdP claims (SEC-02, SEC-03).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID


@dataclass(frozen=True)
class Identity:
    user_id: UUID
    tenant_id: UUID
    roles: tuple[str, ...] = field(default_factory=tuple)
    email: str | None = None
