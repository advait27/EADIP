"""Tool Agent (Phase 8, FR-033): capability matching + health/latency/cost
tie-break + ACL pre-filter, returning ranked fallback candidates (AP-8)."""

from __future__ import annotations

from uuid import UUID

from eadip.mcp.agent import ToolAgent
from eadip.mcp.models import (
    RegisteredServer,
    ServerHealth,
    ServerState,
    ToolPermission,
    ToolServerConfig,
    ToolSpec,
)
from eadip.security.identity import Identity

T = UUID("44444444-4444-4444-4444-444444444444")
ANALYST = Identity(user_id=T, tenant_id=T, roles=("analyst",))


def _server(
    name: str,
    tools: list[ToolSpec],
    *,
    state: ServerState = ServerState.HEALTHY,
    latency_ms: float = 0.0,
    weight_cost: float = 1.0,
) -> RegisteredServer:
    return RegisteredServer(
        tenant_id=T,
        config=ToolServerConfig(name=name, weight_cost=weight_cost),
        tools=tools,
        health=ServerHealth(state=state, last_latency_ms=latency_ms),
    )


def _tool(name: str, desc: str, *, acl: tuple[str, ...] = ()) -> ToolSpec:
    return ToolSpec(name=name, description=desc, permission=ToolPermission(acl_tags=acl))


def test_selects_by_capability_overlap() -> None:
    servers = [
        _server("a", [_tool("fx_rate", "currency exchange rate lookup")]),
        _server("b", [_tool("weather", "current weather forecast")]),
    ]
    picks = ToolAgent().select("exchange rate for currency", servers, ANALYST)
    assert picks and picks[0].tool == "fx_rate"


def test_no_match_returns_empty() -> None:
    servers = [_server("a", [_tool("weather", "current weather forecast")])]
    assert ToolAgent().select("gross margin drivers", servers, ANALYST) == []


def test_failed_and_draining_servers_are_skipped() -> None:
    tool = [_tool("fx_rate", "currency exchange rate lookup")]
    servers = [
        _server("down", tool, state=ServerState.FAILED),
        _server("drain", tool, state=ServerState.DRAINING),
    ]
    assert ToolAgent().select("exchange rate currency", servers, ANALYST) == []


def test_tie_broken_by_health_then_latency() -> None:
    cap = "currency exchange rate lookup"
    servers = [
        _server("slow", [_tool("fx_rate", cap)], latency_ms=500.0),
        _server("fast", [_tool("fx_rate", cap)], latency_ms=10.0),
        _server("degraded", [_tool("fx_rate", cap)], state=ServerState.DEGRADED, latency_ms=1.0),
    ]
    picks = ToolAgent().select("exchange rate currency", servers, ANALYST)
    # equal capability score -> healthy beats degraded; among healthy, fast first.
    assert [p.server for p in picks[:2]] == ["fast", "slow"]
    assert picks[-1].server == "degraded"


def test_acl_tags_prefilter() -> None:
    servers = [
        _server("restricted", [_tool("fx_rate", "currency exchange rate", acl=("finance",))]),
    ]
    # analyst lacks the "finance" ACL tag -> filtered out (advisory pre-check).
    assert ToolAgent().select("exchange rate currency", servers, ANALYST) == []
    finance = Identity(user_id=T, tenant_id=T, roles=("analyst", "finance"))
    assert ToolAgent().select("exchange rate currency", servers, finance)


def test_returns_ordered_candidates_for_fallback() -> None:
    cap = "currency exchange rate lookup"
    servers = [
        _server("primary", [_tool("fx_rate", cap)], latency_ms=5.0),
        _server("backup", [_tool("fx_rate", cap)], latency_ms=50.0),
    ]
    picks = ToolAgent().select("exchange rate currency", servers, ANALYST)
    assert [p.server for p in picks] == ["primary", "backup"]  # fallback order
