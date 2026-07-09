"""Model-routing policy (Phase 11, FR-053, US-D3, TAD Ch 10).

Per-task tiering with two hard rules:
  - **Pins**: planning and verification are pinned to the STRONG tier — the
    tasks where a weak model silently corrupts everything downstream. Admin
    overrides that would route a pinned task below STRONG are refused.
  - **Budget-aware degradation**: when the remaining run/tenant budget cannot
    afford the chosen tier's estimated per-call cost, unpinned tasks degrade one
    tier at a time (STRONG→STANDARD→LIGHT); pinned tasks never degrade.

Deterministic and settings-driven; the LLM-backed agents receive their model
from `route(...)` instead of a single global default.
"""

from __future__ import annotations

from eadip.platform.models import ModelChoice, ModelTier, TaskKind


class RoutingGuardrailError(Exception):
    """Raised when an override would violate a routing guardrail (e.g. unpin
    verification from the strong tier)."""


# Verification/planning correctness anchors the whole platform (AP-1/AP-3).
PINNED_STRONG: frozenset[TaskKind] = frozenset({TaskKind.PLAN, TaskKind.VERIFY})

_DEFAULT_TIERS: dict[TaskKind, ModelTier] = {
    TaskKind.INTERPRET: ModelTier.STANDARD,
    TaskKind.PLAN: ModelTier.STRONG,
    TaskKind.REFLECT: ModelTier.STANDARD,
    TaskKind.VERIFY: ModelTier.STRONG,
    TaskKind.RECOMMEND: ModelTier.STANDARD,
    TaskKind.SQL_GENERATE: ModelTier.STANDARD,
    TaskKind.QUERY_EXPAND: ModelTier.LIGHT,
    TaskKind.RERANK: ModelTier.LIGHT,
    TaskKind.EMBED: ModelTier.LIGHT,
}

_DEGRADE: dict[ModelTier, ModelTier] = {
    ModelTier.STRONG: ModelTier.STANDARD,
    ModelTier.STANDARD: ModelTier.LIGHT,
}


class ModelRoutingPolicy:
    def __init__(
        self,
        *,
        tier_models: dict[ModelTier, str],
        tier_cost_estimate_usd: dict[ModelTier, float] | None = None,
        overrides: dict[TaskKind, ModelTier] | None = None,
    ) -> None:
        self._models = dict(tier_models)
        self._costs = dict(
            tier_cost_estimate_usd
            or {ModelTier.STRONG: 0.02, ModelTier.STANDARD: 0.002, ModelTier.LIGHT: 0.0005}
        )
        self._overrides: dict[TaskKind, ModelTier] = {}
        for task, tier in (overrides or {}).items():
            self.set_tier(task, tier)

    def tier_for(self, task: TaskKind) -> ModelTier:
        if task in PINNED_STRONG:
            return ModelTier.STRONG
        return self._overrides.get(task, _DEFAULT_TIERS.get(task, ModelTier.STANDARD))

    def set_tier(self, task: TaskKind, tier: ModelTier) -> None:
        """Admin override, guardrailed: pinned tasks cannot leave STRONG."""
        if task in PINNED_STRONG and tier is not ModelTier.STRONG:
            raise RoutingGuardrailError(
                f"task '{task}' is pinned to the strong tier (verification/planning "
                f"correctness anchors the platform) and cannot be routed to '{tier}'"
            )
        self._overrides[task] = tier

    def route(self, task: TaskKind, *, remaining_budget_usd: float | None = None) -> ModelChoice:
        """Choose the model for a task, degrading unpinned tiers the remaining
        budget cannot afford."""
        tier = self.tier_for(task)
        pinned = task in PINNED_STRONG
        reason = "default tier" if task not in self._overrides else "admin override"
        if pinned:
            reason = "pinned strong (planning/verification)"
        elif remaining_budget_usd is not None:
            while remaining_budget_usd < self._costs.get(tier, 0.0) and tier in _DEGRADE:
                tier = _DEGRADE[tier]
                reason = "degraded to fit remaining budget"
        return ModelChoice(
            task=task, tier=tier, model=self._models[tier], pinned=pinned, reason=reason
        )

    def table(self) -> list[ModelChoice]:
        """The full routing table (for the admin portal)."""
        return [self.route(task) for task in TaskKind]
