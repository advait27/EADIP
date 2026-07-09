"""Autonomy policy (Phase 9, FR-034): reads auto-run; writes/high-impact gate."""

from __future__ import annotations

from eadip.approval.models import ProposedAction
from eadip.approval.policy import AutonomyPolicy
from eadip.security.rbac import Effect


def _action(effect: Effect) -> ProposedAction:
    return ProposedAction(step_id="s", kind="tool", effect=effect, tool="t")


def test_reads_run_autonomously() -> None:
    policy = AutonomyPolicy.safe_default()
    assert policy.requires_approval(_action(Effect.READ)) is False


def test_writes_and_high_impact_are_gated_by_default() -> None:
    policy = AutonomyPolicy.safe_default()
    assert policy.requires_approval(_action(Effect.WRITE)) is True
    assert policy.requires_approval(_action(Effect.HIGH_IMPACT)) is True


def test_policy_is_configurable_per_effect() -> None:
    # US-C2: a tenant could auto-approve writes (still deny-by-default for the rest).
    policy = AutonomyPolicy(auto_effects=frozenset({Effect.READ, Effect.WRITE}))
    assert policy.requires_approval(_action(Effect.WRITE)) is False
    assert policy.requires_approval(_action(Effect.HIGH_IMPACT)) is True
