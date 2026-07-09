"""Assemble the MCP stack from settings (AP-10).

Builds the MCP Manager (registry + breaker + governed invocation), the Tool Agent
(capability matching), and the orchestrator ToolExecutor. The default registry is
in-memory and offline-deterministic; the Postgres store (RLS-scoped) is selected
by settings. A `TransportProvider` maps a server config onto a transport — the
in-process demo server is wired here so the platform ships with a governed sample
tool; real http/stdio MCP transports slot in behind `build_transport`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from uuid import UUID

from eadip.config.settings import Settings
from eadip.mcp.agent import ToolAgent
from eadip.mcp.breaker import CircuitBreaker
from eadip.mcp.demo_tools import DEMO_CONFIG, build_demo_transport
from eadip.mcp.executor import ToolExecutor
from eadip.mcp.models import ToolServerConfig
from eadip.mcp.registry import InMemoryServerStore, McpManager, ServerStore
from eadip.mcp.transport import ToolTransport, build_transport
from eadip.security.policy import PolicyDecisionPoint
from eadip.security.vault import SecretsProvider

# The provider callable: (server config, resolved credential) -> transport.
TransportProviderCallable = Callable[[ToolServerConfig, "str | None"], ToolTransport]


async def _seed_demo_server(manager: McpManager, tenant_id: UUID) -> None:
    """Register the deterministic demo server for a tenant on first use (dev),
    so the orchestrator has a governed external tool to call without a portal
    call. Idempotent — re-registration just refreshes discovery."""
    await manager.register(tenant_id, DEMO_CONFIG)


def build_transport_provider() -> TransportProviderCallable:
    """A provider that returns the demo in-process transport for the demo server
    and builds a real transport (lazily) for everything else."""
    demo = build_demo_transport()

    def provider(config: ToolServerConfig, credential: str | None) -> ToolTransport:
        if config.name == DEMO_CONFIG.name or config.transport == "inprocess":
            return demo
        return build_transport(config.transport, config.endpoint, credential=credential)

    return provider


def build_mcp_manager(
    settings: Settings,
    pdp: PolicyDecisionPoint,
    *,
    store: ServerStore | None = None,
    secrets: SecretsProvider | None = None,
) -> McpManager:
    breaker = CircuitBreaker(
        failure_threshold=settings.mcp_failure_threshold,
        base_cooldown_s=settings.mcp_breaker_cooldown_s,
        max_cooldown_s=settings.mcp_breaker_max_cooldown_s,
        degrade_latency_ms=settings.mcp_degrade_latency_ms,
    )
    seed: Callable[[McpManager, UUID], Awaitable[None]] | None = (
        _seed_demo_server if settings.mcp_register_demo_server else None
    )
    return McpManager(
        store or InMemoryServerStore(),
        pdp,
        transport_provider=build_transport_provider(),
        breaker=breaker,
        secrets=secrets,
        invoke_timeout_s=settings.mcp_invoke_timeout_s,
        seed=seed,
    )


def build_tool_executor(settings: Settings, manager: McpManager) -> ToolExecutor:
    return ToolExecutor(manager, ToolAgent(), max_fallbacks=settings.mcp_max_fallbacks)
