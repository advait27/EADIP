"""Build the platform services from settings (Phase 11).

Everything defaults to in-process/deterministic (dev/tests need no external
services); Postgres-backed stores swap in behind the same ports when the
respective backend flag is set and the database is enabled.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from eadip.agents.interpreter import INTERPRETER_INSTRUCTION
from eadip.agents.planner import PLANNER_INSTRUCTION
from eadip.agents.reflection import REFLECTION_INSTRUCTION
from eadip.config.settings import Settings
from eadip.platform.budgets import BudgetLedger, BudgetStore, InMemoryBudgetStore
from eadip.platform.flags import FeatureFlagService
from eadip.platform.models import ModelTier
from eadip.platform.notifications import (
    InMemoryChannel,
    LogChannel,
    NotificationChannel,
    NotificationService,
    WebhookChannel,
)
from eadip.platform.prompts import InMemoryPromptStore, PromptRegistry, PromptStore
from eadip.platform.ratelimit import ConcurrencyGate, RateLimiter
from eadip.platform.residency import InMemoryResidencyStore, ResidencyPolicy, ResidencyStore
from eadip.platform.routing import ModelRoutingPolicy

if TYPE_CHECKING:
    from eadip.adapters.postgres import Database

# The shipped agent instructions, installed as version-1 ACTIVE prompt artifacts
# at bootstrap; every change after that goes through the eval-gated lifecycle.
DEFAULT_PROMPTS: dict[str, str] = {
    "agent/interpreter": INTERPRETER_INSTRUCTION,
    "agent/planner": PLANNER_INSTRUCTION,
    "agent/reflection": REFLECTION_INSTRUCTION,
}


def build_prompt_registry(
    settings: Settings, *, database: Database | None = None
) -> PromptRegistry:
    store: PromptStore
    if settings.prompt_backend == "postgres" and database is not None:
        from eadip.adapters.postgres_platform import PostgresPromptStore

        store = PostgresPromptStore(database)
    else:
        store = InMemoryPromptStore()
    return PromptRegistry(store, eval_threshold=settings.prompt_eval_threshold, now=time.time)


async def seed_default_prompts(registry: PromptRegistry) -> None:
    for name, content in DEFAULT_PROMPTS.items():
        await registry.seed_default(name, content)


def build_routing_policy(settings: Settings) -> ModelRoutingPolicy:
    return ModelRoutingPolicy(
        tier_models={
            ModelTier.STRONG: settings.model_tier_strong,
            ModelTier.STANDARD: settings.model_tier_standard,
            ModelTier.LIGHT: settings.model_tier_light,
        },
        tier_cost_estimate_usd={
            ModelTier.STRONG: settings.routing_cost_strong_usd,
            ModelTier.STANDARD: settings.routing_cost_standard_usd,
            ModelTier.LIGHT: settings.routing_cost_light_usd,
        },
    )


def build_budget_ledger(settings: Settings, *, database: Database | None = None) -> BudgetLedger:
    store: BudgetStore
    if settings.budget_backend == "postgres" and database is not None:
        from eadip.adapters.postgres_platform import PostgresBudgetStore

        store = PostgresBudgetStore(database)
    else:
        store = InMemoryBudgetStore()
    return BudgetLedger(store)


def build_notification_service(
    settings: Settings, *, inbox: InMemoryChannel | None = None
) -> NotificationService:
    channels: list[NotificationChannel] = []
    for name in settings.notification_channels:
        if name == "inbox":
            channels.append(inbox if inbox is not None else InMemoryChannel())
        elif name == "log":
            channels.append(LogChannel())
        elif name == "webhook" and settings.notification_webhook_url:
            channels.append(WebhookChannel(settings.notification_webhook_url))
    return NotificationService(channels, now=time.time)


def build_residency_policy(
    settings: Settings, *, database: Database | None = None
) -> ResidencyPolicy:
    store: ResidencyStore
    if settings.residency_backend == "postgres" and database is not None:
        from eadip.adapters.postgres_platform import PostgresResidencyStore

        store = PostgresResidencyStore(database)
    else:
        store = InMemoryResidencyStore()
    return ResidencyPolicy(store, deployment_region=settings.deployment_region)


def build_flag_service(settings: Settings) -> FeatureFlagService:
    return FeatureFlagService(settings.feature_flags)


def build_rate_limiter(settings: Settings) -> RateLimiter:
    return RateLimiter(rate_per_s=settings.rate_limit_rate_per_s, burst=settings.rate_limit_burst)


def build_concurrency_gate(settings: Settings) -> ConcurrencyGate:
    return ConcurrencyGate(settings.max_concurrent_streams_per_tenant)
