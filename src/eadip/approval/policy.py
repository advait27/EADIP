"""Autonomy policy (FR-034, US-C2, AP-2): what may run without a human.

Per-action-type autonomy — reads run autonomously; writes and high-impact
actions are gated by default. The policy is configurable per effect so a tenant
can, e.g., auto-approve low-risk writes to a specific domain later, but the
*default is deny-by-default for anything side-effecting* — the safe direction.

Pure logic, no IO. The engine consults it before every side-effecting step.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from eadip.approval.models import ProposedAction
from eadip.security.rbac import Effect


@dataclass(frozen=True)
class AutonomyPolicy:
    """Which effects may run without human approval. Everything not listed as
    auto is gated (deny-by-default)."""

    # Reads are autonomous; writes and high-impact are gated unless explicitly
    # added here by an admin (future: per-tenant/domain overrides).
    auto_effects: frozenset[Effect] = field(default_factory=lambda: frozenset({Effect.READ}))

    def requires_approval(self, action: ProposedAction) -> bool:
        """True when this action must pause for human approval."""
        return action.effect not in self.auto_effects

    @staticmethod
    def safe_default() -> AutonomyPolicy:
        """Read-only autonomy — the locked safety guarantee (G5): zero unapproved
        writes. Any write/high-impact action pauses for a human."""
        return AutonomyPolicy(auto_effects=frozenset({Effect.READ}))
