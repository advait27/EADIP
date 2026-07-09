"""ToolExecutor (Phase 8): a governed tool as an orchestrator step — selection,
governed invocation, fallback on failure (AP-8), and tool-provenance findings."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from eadip.mcp.agent import ToolAgent
from eadip.mcp.breaker import CircuitBreaker
from eadip.mcp.executor import ToolExecutor
from eadip.mcp.models import ToolPermission, ToolServerConfig, ToolSpec
from eadip.mcp.registry import InMemoryServerStore, McpManager
from eadip.mcp.transport import InProcessTransport
from eadip.orchestrator.models import PlanStep
from eadip.orchestrator.state import RunState
from eadip.security.policy import PolicyDecisionPoint
from eadip.security.rbac import Effect, default_catalog

T = UUID("55555555-5555-5555-5555-555555555555")

FX = ToolSpec(
    name="fx_rate",
    description="currency exchange rate lookup",
    input_schema={
        "type": "object",
        "properties": {"currency": {"type": "string", "enum": ["EUR"]}},
        "required": ["currency"],
        "additionalProperties": False,
    },
    permission=ToolPermission(effect=Effect.READ, domains=("metrics",)),
)


def _state() -> RunState:
    return RunState(run_id=T, tenant_id=T, user_id=T, question="q", acl_tags=("analyst",))


def _manager(transport: InProcessTransport, name: str = "svc") -> McpManager:
    return McpManager(
        InMemoryServerStore(),
        PolicyDecisionPoint(default_catalog()),
        transport_provider=lambda cfg, cred: transport,
        breaker=CircuitBreaker(failure_threshold=1),
    )


async def _fx(args: dict[str, Any]) -> Any:
    return {"currency": args["currency"], "rate": 1.08}


async def test_tool_step_produces_provenance_finding() -> None:
    transport = InProcessTransport([FX], {"fx_rate": _fx})
    mgr = _manager(transport)
    await mgr.register(T, ToolServerConfig(name="svc", transport="inprocess"))
    ex = ToolExecutor(mgr, ToolAgent())
    step = PlanStep(
        id="tool-1",
        kind="tool",
        description="currency exchange rate for EUR",
        params={"intent": "exchange rate currency EUR", "arguments": {"currency": "EUR"}},
    )
    result = await ex.execute(step, _state())
    assert result.ok and result.findings
    f = result.findings[0]
    assert f.source == "tool"
    assert f.evidence[0].ref == "svc.fx_rate"
    assert f.detail["untrusted"] is True


async def test_no_capable_tool_returns_failed_step() -> None:
    transport = InProcessTransport([FX], {"fx_rate": _fx})
    mgr = _manager(transport)
    await mgr.register(T, ToolServerConfig(name="svc", transport="inprocess"))
    ex = ToolExecutor(mgr, ToolAgent())
    step = PlanStep(id="t", kind="tool", description="ship a rocket to mars", params={})
    result = await ex.execute(step, _state())
    assert not result.ok and result.error == "no_tool_match"


async def test_falls_back_to_second_server_on_transport_failure() -> None:
    # Two servers expose the same capability; the first is unhealthy, so the
    # executor falls back to the second (narrowed-scope continuation, AP-8).
    down = InProcessTransport([FX], {"fx_rate": _fx}, healthy=False)
    up = InProcessTransport([FX], {"fx_rate": _fx}, healthy=True)
    transports = {"down": down, "up": up}
    mgr = McpManager(
        InMemoryServerStore(),
        PolicyDecisionPoint(default_catalog()),
        transport_provider=lambda cfg, cred: transports[cfg.name],
        breaker=CircuitBreaker(failure_threshold=5),
    )
    # Register "up" healthy; "down" will fail discovery, so force it registered
    # with tools by discovering while healthy then flipping it down.
    down.set_healthy(True)
    await mgr.register(T, ToolServerConfig(name="down", transport="inprocess", weight_latency=0.0))
    await mgr.register(T, ToolServerConfig(name="up", transport="inprocess", weight_latency=1.0))
    down.set_healthy(False)
    ex = ToolExecutor(mgr, ToolAgent(), max_fallbacks=2)
    step = PlanStep(
        id="t",
        kind="tool",
        description="exchange rate currency",
        params={"intent": "exchange rate currency EUR", "arguments": {"currency": "EUR"}},
    )
    result = await ex.execute(step, _state())
    assert result.ok
    assert result.findings[0].detail["server"] == "up"
    assert result.findings[0].detail["fallback_of"] == "down.fx_rate"
