"""Platform services (Phase 11): model routing (pins, overrides, budget-aware
degradation), per-tenant budgets, notifications, feature flags, rate limiting
and stream backpressure."""

from __future__ import annotations

from uuid import uuid4

import pytest

from eadip.orchestrator.models import Event
from eadip.platform.budgets import BudgetLedger, InMemoryBudgetStore
from eadip.platform.flags import FeatureFlagService
from eadip.platform.models import ModelTier, Notification, NotificationKind, TaskKind
from eadip.platform.notifications import (
    InMemoryChannel,
    NotificationService,
    notifications_from_event,
)
from eadip.platform.ratelimit import ConcurrencyGate, RateLimiter
from eadip.platform.routing import ModelRoutingPolicy, RoutingGuardrailError

_TIERS = {
    ModelTier.STRONG: "model-strong",
    ModelTier.STANDARD: "model-standard",
    ModelTier.LIGHT: "model-light",
}


def _policy() -> ModelRoutingPolicy:
    return ModelRoutingPolicy(
        tier_models=_TIERS,
        tier_cost_estimate_usd={
            ModelTier.STRONG: 0.02,
            ModelTier.STANDARD: 0.002,
            ModelTier.LIGHT: 0.0005,
        },
    )


# --- model routing (FR-053) ----------------------------------------------------
def test_planning_and_verification_pinned_strong() -> None:
    policy = _policy()
    for task in (TaskKind.PLAN, TaskKind.VERIFY):
        choice = policy.route(task, remaining_budget_usd=0.0001)  # broke: still strong
        assert choice.tier is ModelTier.STRONG and choice.pinned
        assert choice.model == "model-strong"


def test_pinned_tasks_refuse_downgrade_overrides() -> None:
    policy = _policy()
    with pytest.raises(RoutingGuardrailError):
        policy.set_tier(TaskKind.VERIFY, ModelTier.LIGHT)


def test_admin_override_and_budget_degradation_for_unpinned_tasks() -> None:
    policy = _policy()
    policy.set_tier(TaskKind.INTERPRET, ModelTier.STRONG)
    assert policy.route(TaskKind.INTERPRET).tier is ModelTier.STRONG
    # With almost no budget left, the unpinned task degrades to light.
    degraded = policy.route(TaskKind.INTERPRET, remaining_budget_usd=0.001)
    assert degraded.tier is ModelTier.LIGHT
    assert degraded.reason == "degraded to fit remaining budget"


def test_routing_table_covers_every_task() -> None:
    table = _policy().table()
    assert {c.task for c in table} == set(TaskKind)


# --- per-tenant budgets (FR-053) -------------------------------------------------
async def test_budget_cap_charge_and_refusal() -> None:
    ledger = BudgetLedger(InMemoryBudgetStore())
    tenant = uuid4()
    assert await ledger.allows_new_run(tenant)  # no cap configured -> allowed
    await ledger.set_cap(tenant, 1.00)
    await ledger.charge(tenant, 0.60)
    assert await ledger.allows_new_run(tenant)
    await ledger.charge(tenant, 0.50)
    status = await ledger.status(tenant)
    assert status.exhausted
    assert not await ledger.allows_new_run(tenant)
    await ledger.reset_spend(tenant)  # monthly rollover
    assert await ledger.allows_new_run(tenant)


# --- notifications (FR-056) ------------------------------------------------------
async def test_publish_delivers_to_inbox() -> None:
    inbox = InMemoryChannel()
    service = NotificationService([inbox], now=lambda: 42.0)
    tenant = uuid4()
    note = await service.publish(
        Notification(
            tenant_id=tenant,
            kind=NotificationKind.RUN_COMPLETED,
            title="Run completed",
        )
    )
    assert note.delivered and note.channel == "inbox" and note.created_at_s == 42.0
    assert [n.title for n in inbox.list(tenant)] == ["Run completed"]
    assert inbox.list(uuid4()) == []  # tenant-scoped


def test_stream_events_map_to_notifications() -> None:
    tenant, run_id = uuid4(), uuid4()
    approval = notifications_from_event(
        tenant, run_id, Event(type="approval.required", data={"action": {"summary": "open"}})
    )
    assert [n.kind for n in approval] == [NotificationKind.APPROVAL_REQUIRED]
    assert approval[0].severity == "action_required"
    done = notifications_from_event(
        tenant, run_id, Event(type="run.done", data={"status": "done", "findings": 3})
    )
    assert [n.kind for n in done] == [NotificationKind.RUN_COMPLETED]
    anomaly = notifications_from_event(
        tenant, run_id, Event(type="finding.partial", data={"kind": "anomaly", "claim": "spike"})
    )
    assert [n.kind for n in anomaly] == [NotificationKind.ANOMALY_DETECTED]
    # Non-notifiable events map to nothing.
    assert notifications_from_event(tenant, run_id, Event(type="step.started", data={})) == []
    driver = Event(type="finding.partial", data={"kind": "driver"})
    assert notifications_from_event(tenant, run_id, driver) == []


# --- feature flags (FR-057) --------------------------------------------------------
def test_flags_seed_runtime_override_and_deny_by_default() -> None:
    flags = FeatureFlagService({"new_brief_layout": True})
    assert flags.is_enabled("new_brief_layout")
    assert not flags.is_enabled("unknown_flag")  # deny-by-default
    flags.set("new_brief_layout", False)
    flags.set("beta_graph_view", True)
    assert flags.all() == {"beta_graph_view": True, "new_brief_layout": False}


# --- rate limiting + backpressure (NFR-10/13) ---------------------------------------
def test_token_bucket_refuses_burst_overflow_then_refills() -> None:
    clock = {"t": 0.0}
    limiter = RateLimiter(rate_per_s=1.0, burst=2, now=lambda: clock["t"])
    assert limiter.allow("tenant-a")[0]
    assert limiter.allow("tenant-a")[0]
    allowed, retry_after = limiter.allow("tenant-a")
    assert not allowed and retry_after > 0
    assert limiter.allow("tenant-b")[0]  # per-key isolation
    clock["t"] = 1.5  # refill 1.5 tokens
    assert limiter.allow("tenant-a")[0]


def test_rate_limit_middleware_throttles_v1_but_not_health() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from eadip.platform.ratelimit import RateLimitMiddleware

    app = FastAPI()
    app.add_middleware(
        RateLimitMiddleware, limiter=RateLimiter(rate_per_s=0.0, burst=2, now=lambda: 0.0)
    )

    @app.get("/v1/echo")
    async def echo() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/healthz")
    async def health() -> dict[str, bool]:
        return {"ok": True}

    client = TestClient(app)
    headers = {"X-Tenant-Id": "tenant-a"}
    assert client.get("/v1/echo", headers=headers).status_code == 200
    assert client.get("/v1/echo", headers=headers).status_code == 200
    throttled = client.get("/v1/echo", headers=headers)
    assert throttled.status_code == 429 and "Retry-After" in throttled.headers
    # Another tenant is unaffected; health endpoints are never throttled.
    assert client.get("/v1/echo", headers={"X-Tenant-Id": "tenant-b"}).status_code == 200
    assert client.get("/healthz", headers=headers).status_code == 200


def test_concurrency_gate_bounds_open_streams_per_tenant() -> None:
    gate = ConcurrencyGate(2)
    assert gate.try_acquire("t1") and gate.try_acquire("t1")
    assert not gate.try_acquire("t1")  # backpressure at the cap
    assert gate.try_acquire("t2")  # other tenants unaffected
    gate.release("t1")
    assert gate.try_acquire("t1")
    gate.release("t1")
    gate.release("t1")
    gate.release("t1")  # over-release stays safe
    assert gate.open_count("t1") == 0
