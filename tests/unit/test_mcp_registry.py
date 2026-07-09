"""MCP Manager (Phase 8, FR-030..033): register→discover→health→lifecycle and
the governed invoke path (RBAC pre-check, arg gate, breaker, cache, SEC-06)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from eadip.mcp.breaker import CircuitBreaker
from eadip.mcp.models import (
    ServerState,
    ToolPermission,
    ToolServerConfig,
    ToolSpec,
)
from eadip.mcp.registry import InMemoryServerStore, McpManager
from eadip.mcp.transport import InProcessTransport, ToolTransport
from eadip.security.identity import Identity
from eadip.security.policy import PolicyDecisionPoint
from eadip.security.rbac import Effect, default_catalog

T = UUID("22222222-2222-2222-2222-222222222222")
ANALYST = Identity(user_id=T, tenant_id=T, roles=("analyst",))
APPROVER = Identity(user_id=T, tenant_id=T, roles=("approver",))

READ_TOOL = ToolSpec(
    name="fx_rate",
    description="exchange rate lookup",
    input_schema={
        "type": "object",
        "properties": {"currency": {"type": "string", "enum": ["EUR", "USD"]}},
        "required": ["currency"],
        "additionalProperties": False,
    },
    permission=ToolPermission(effect=Effect.READ, domains=("metrics",)),
    idempotent=True,
)
WRITE_TOOL = ToolSpec(
    name="open_ticket",
    description="open a ticket",
    input_schema={
        "type": "object",
        "properties": {"title": {"type": "string"}},
        "required": ["title"],
        "additionalProperties": False,
    },
    permission=ToolPermission(effect=Effect.WRITE, domains=("tickets",)),
    idempotent=False,
)

_CONFIG = ToolServerConfig(name="svc", transport="inprocess")


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def _transport(*, healthy: bool = True) -> InProcessTransport:
    calls = {"n": 0}

    async def fx(args: dict[str, Any]) -> Any:
        calls["n"] += 1
        return {"currency": args["currency"], "rate": 1.08, "calls": calls["n"]}

    async def ticket(args: dict[str, Any]) -> Any:
        return {"id": "T-1", "title": args["title"]}

    return InProcessTransport(
        specs=[READ_TOOL, WRITE_TOOL],
        handlers={"fx_rate": fx, "open_ticket": ticket},
        healthy=healthy,
    )


def _manager(transport: ToolTransport, now: Clock | None = None) -> McpManager:
    clock = now or Clock()
    return McpManager(
        InMemoryServerStore(),
        PolicyDecisionPoint(default_catalog()),
        transport_provider=lambda cfg, cred: transport,
        breaker=CircuitBreaker(failure_threshold=2, base_cooldown_s=5.0, now=clock),
        now=clock,
    )


async def test_register_discovers_and_caches_tool_schemas() -> None:
    mgr = _manager(_transport())
    server = await mgr.register(T, _CONFIG)
    assert server.health.state is ServerState.HEALTHY
    assert {t.name for t in server.tools} == {"fx_rate", "open_ticket"}
    assert server.tool("fx_rate").input_schema["required"] == ["currency"]


async def test_register_marks_failed_when_server_unreachable() -> None:
    mgr = _manager(_transport(healthy=False))
    server = await mgr.register(T, _CONFIG)
    assert server.health.state is ServerState.FAILED
    assert server.tools == []


async def test_read_tool_allowed_for_analyst_and_cached() -> None:
    mgr = _manager(_transport())
    await mgr.register(T, _CONFIG)
    r1 = await mgr.invoke(ANALYST, "svc", "fx_rate", {"currency": "EUR"})
    assert r1.ok and r1.output["untrusted"] is True  # SEC-06 wrapped
    assert r1.output["data"]["calls"] == 1
    r2 = await mgr.invoke(ANALYST, "svc", "fx_rate", {"currency": "EUR"})
    assert r2.from_cache and r2.output["data"]["calls"] == 1  # not re-invoked


async def test_write_tool_denied_for_analyst_but_allowed_for_approver() -> None:
    mgr = _manager(_transport())
    await mgr.register(T, _CONFIG)
    denied = await mgr.invoke(ANALYST, "svc", "open_ticket", {"title": "x"})
    assert not denied.ok and denied.reason == "rbac_denied"
    ok = await mgr.invoke(APPROVER, "svc", "open_ticket", {"title": "x"})
    assert ok.ok and ok.output["data"]["id"] == "T-1"


async def test_write_tool_is_not_cached() -> None:
    mgr = _manager(_transport())
    await mgr.register(T, _CONFIG)
    r1 = await mgr.invoke(APPROVER, "svc", "open_ticket", {"title": "x"})
    r2 = await mgr.invoke(APPROVER, "svc", "open_ticket", {"title": "x"})
    assert not r1.from_cache and not r2.from_cache


async def test_invalid_arguments_are_refused_before_invocation() -> None:
    mgr = _manager(_transport())
    await mgr.register(T, _CONFIG)
    r = await mgr.invoke(ANALYST, "svc", "fx_rate", {"currency": "XXX"})
    assert not r.ok and r.reason == "invalid_arguments"


async def test_unknown_server_and_tool() -> None:
    mgr = _manager(_transport())
    await mgr.register(T, _CONFIG)
    assert (await mgr.invoke(ANALYST, "nope", "fx_rate", {})).reason == "unknown_server"
    assert (await mgr.invoke(ANALYST, "svc", "nope", {})).reason == "unknown_tool"


async def test_transport_failures_trip_breaker_then_fail_fast() -> None:
    clock = Clock()
    transport = _transport()
    mgr = _manager(transport, now=clock)
    await mgr.register(T, _CONFIG)
    transport.set_healthy(False)
    # 2 failures at threshold=2 -> breaker opens.
    await mgr.invoke(ANALYST, "svc", "fx_rate", {"currency": "EUR"})
    r = await mgr.invoke(ANALYST, "svc", "fx_rate", {"currency": "EUR"})
    assert r.reason == "transport_error"
    r = await mgr.invoke(ANALYST, "svc", "fx_rate", {"currency": "EUR"})
    assert r.reason == "circuit_open"  # fail fast, no transport call
    # After cooldown + server recovery, a probe re-closes it.
    clock.t = 5.0
    transport.set_healthy(True)
    r = await mgr.invoke(ANALYST, "svc", "fx_rate", {"currency": "EUR"})
    assert r.ok


async def test_drain_blocks_new_invocations() -> None:
    mgr = _manager(_transport())
    await mgr.register(T, _CONFIG)
    await mgr.drain(T, "svc")
    r = await mgr.invoke(ANALYST, "svc", "fx_rate", {"currency": "EUR"})
    assert r.reason == "circuit_open"


async def test_tenant_isolation_of_registry() -> None:
    mgr = _manager(_transport())
    await mgr.register(T, _CONFIG)
    other = UUID("33333333-3333-3333-3333-333333333333")
    assert await mgr.list_servers(other) == []
