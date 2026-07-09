"""Build the autonomy policy from settings (Phase 9)."""

from __future__ import annotations

from eadip.approval.policy import AutonomyPolicy
from eadip.config.settings import Settings
from eadip.security.rbac import Effect


def build_autonomy_policy(settings: Settings) -> AutonomyPolicy:
    """Read the configured auto-effects. When approval is disabled (dev only),
    everything may auto-run; otherwise anything not listed as auto is gated —
    deny-by-default for side-effecting actions."""
    if not settings.approval_enabled:
        return AutonomyPolicy(auto_effects=frozenset(Effect))
    effects = frozenset(Effect(e) for e in settings.autonomy_auto_effects)
    return AutonomyPolicy(auto_effects=effects or frozenset({Effect.READ}))
