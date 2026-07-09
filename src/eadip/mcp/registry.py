"""MCP Manager (FR-030/031, TAD Ch 9) — the extensibility backbone.

Lifecycle for every onboarded system:

    register  → persist config, resolve credential via vault (SEC-05)
    discover  → list tools, cache their JSON schemas (idempotent-read cacheable)
    health    → probe; the circuit breaker drives Healthy/Degraded/Failed/Draining
    invoke    → RBAC pre-check → typed-arg gate → breaker-guarded call → cache

A new server registered through the portal appears in discovery, is health-checked
and is invocable subject to RBAC — with **no code deploy** (the DoD). Registration
is per tenant; the in-process store keeps tenants separate and the Postgres store
adds RLS. The manager holds live transports keyed by (tenant, server); a `TransportProvider`
builds them, so tests inject deterministic in-process servers.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Protocol
from uuid import UUID

from eadip.mcp.breaker import CircuitBreaker
from eadip.mcp.models import (
    RegisteredServer,
    ServerHealth,
    ServerState,
    ToolResult,
    ToolServerConfig,
    ToolSpec,
    ToolValidationError,
)
from eadip.mcp.permissions import authorize_tool, wrap_untrusted
from eadip.mcp.transport import ToolTransport, TransportError
from eadip.mcp.validate import validate_arguments
from eadip.security.identity import Identity
from eadip.security.policy import PolicyDecisionPoint

# Given a server config + resolved credential, build (or return) its transport.
TransportProvider = Callable[[ToolServerConfig, str | None], ToolTransport]


class ServerStore(Protocol):
    """Persistence for registered servers (in-memory dev / RLS Postgres prod)."""

    async def upsert(self, server: RegisteredServer) -> None: ...

    async def get(self, tenant_id: UUID, name: str) -> RegisteredServer | None: ...

    async def list(self, tenant_id: UUID) -> list[RegisteredServer]: ...


class InMemoryServerStore:
    def __init__(self) -> None:
        self._by_tenant: dict[UUID, dict[str, RegisteredServer]] = {}

    async def upsert(self, server: RegisteredServer) -> None:
        self._by_tenant.setdefault(server.tenant_id, {})[server.config.name] = server

    async def get(self, tenant_id: UUID, name: str) -> RegisteredServer | None:
        return self._by_tenant.get(tenant_id, {}).get(name)

    async def list(self, tenant_id: UUID) -> list[RegisteredServer]:
        return list(self._by_tenant.get(tenant_id, {}).values())


class McpManager:
    def __init__(
        self,
        store: ServerStore,
        pdp: PolicyDecisionPoint,
        *,
        transport_provider: TransportProvider,
        breaker: CircuitBreaker | None = None,
        secrets: object | None = None,  # SecretsProvider (duck-typed .get)
        invoke_timeout_s: float = 15.0,
        seed: Callable[[McpManager, UUID], Awaitable[None]] | None = None,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._store = store
        self._pdp = pdp
        self._provider = transport_provider
        self._breaker = breaker or CircuitBreaker(now=now)
        self._secrets = secrets
        self._invoke_timeout_s = invoke_timeout_s
        self._seed = seed
        self._now = now
        self._transports: dict[tuple[UUID, str], ToolTransport] = {}
        self._cache: dict[tuple[UUID, str, str, str], ToolResult] = {}
        self._seeded: set[UUID] = set()

    async def _ensure_seeded(self, tenant_id: UUID) -> None:
        """Run the one-time seed hook for a tenant (e.g. register the demo server
        in dev) the first time the tenant's registry is touched."""
        if self._seed is None or tenant_id in self._seeded:
            return
        self._seeded.add(tenant_id)
        await self._seed(self, tenant_id)

    # --- lifecycle: register -> discover ------------------------------------
    async def register(self, tenant_id: UUID, config: ToolServerConfig) -> RegisteredServer:
        """Onboard a server and immediately discover its tools (no code deploy).
        Idempotent on (tenant, name): re-registering refreshes config + discovery."""
        transport = self._resolve_transport(tenant_id, config)
        try:
            tools = await transport.discover()
            health = ServerHealth(state=ServerState.HEALTHY)
        except TransportError as exc:
            tools = []
            health = ServerHealth(state=ServerState.FAILED, last_error=str(exc)[:200])
        server = RegisteredServer(
            tenant_id=tenant_id,
            config=config,
            tools=self._apply_default_permissions(config, tools),
            health=health,
            registered_at_s=self._now(),
        )
        await self._store.upsert(server)
        return server

    async def discover(self, tenant_id: UUID, name: str) -> RegisteredServer:
        """Re-list a server's tools and refresh the cached schemas."""
        server = await self._require(tenant_id, name)
        transport = self._resolve_transport(tenant_id, server.config)
        try:
            tools = await transport.discover()
            server.tools = self._apply_default_permissions(server.config, tools)
            server.health = self._breaker.on_success(server.health, latency_ms=0.0)
        except TransportError as exc:
            server.health = self._breaker.on_failure(server.health, error=str(exc))
        await self._store.upsert(server)
        return server

    async def health_check(self, tenant_id: UUID, name: str) -> ServerHealth:
        """Probe a server and advance its breaker state."""
        server = await self._require(tenant_id, name)
        if server.health.state is ServerState.DRAINING:
            return server.health
        transport = self._resolve_transport(tenant_id, server.config)
        start = self._now()
        try:
            await transport.ping()
            latency_ms = (self._now() - start) * 1000.0
            server.health = self._breaker.on_success(server.health, latency_ms=latency_ms)
        except TransportError as exc:
            server.health = self._breaker.on_failure(server.health, error=str(exc))
        await self._store.upsert(server)
        return server.health

    async def drain(self, tenant_id: UUID, name: str) -> RegisteredServer:
        """Quiesce a server: no new invocations (lifecycle DRAINING)."""
        server = await self._require(tenant_id, name)
        server.health = CircuitBreaker.draining(server.health)
        await self._store.upsert(server)
        self._transports.pop((tenant_id, name), None)
        return server

    async def list_servers(self, tenant_id: UUID) -> list[RegisteredServer]:
        await self._ensure_seeded(tenant_id)
        return await self._store.list(tenant_id)

    # --- invocation ----------------------------------------------------------
    async def invoke(
        self,
        identity: Identity,
        server_name: str,
        tool_name: str,
        arguments: dict[str, object],
    ) -> ToolResult:
        """Governed call: RBAC pre-check → arg gate → breaker → transport → cache.
        Never raises for a governance/transport failure — returns a ToolResult
        with ok=False and a reason so the caller (executor) degrades gracefully."""
        await self._ensure_seeded(identity.tenant_id)
        server = await self._store.get(identity.tenant_id, server_name)
        if server is None:
            return ToolResult(server=server_name, tool=tool_name, ok=False, reason="unknown_server")
        spec = server.tool(tool_name)
        if spec is None:
            return ToolResult(server=server_name, tool=tool_name, ok=False, reason="unknown_tool")

        # 1) RBAC pre-invocation (deny-by-default).
        decision = authorize_tool(self._pdp, identity, spec)
        if not decision.permit:
            return ToolResult(
                server=server_name,
                tool=tool_name,
                ok=False,
                reason="rbac_denied",
                error=decision.reason,
            )

        # 2) Typed-argument gate (SEC-06 boundary hardening).
        try:
            safe_args = validate_arguments(spec.input_schema, dict(arguments))
        except ToolValidationError as exc:
            return ToolResult(
                server=server_name,
                tool=tool_name,
                ok=False,
                reason="invalid_arguments",
                error=exc.reason,
            )

        # 3) Idempotent-read cache (only cacheable, side-effect-free tools).
        cache_key = self._cache_key(identity.tenant_id, server_name, tool_name, safe_args)
        if spec.cacheable and cache_key in self._cache:
            cached = self._cache[cache_key]
            return cached.model_copy(update={"from_cache": True})

        # 4) Circuit breaker (fail fast when open).
        if not self._breaker.allows(server.health):
            return ToolResult(
                server=server_name,
                tool=tool_name,
                ok=False,
                reason="circuit_open",
                error=f"server {server.health.state}",
            )

        # 5) Invoke via the transport, recording the outcome to the breaker.
        transport = self._resolve_transport(identity.tenant_id, server.config)
        start = self._now()
        try:
            raw = await transport.invoke(tool_name, safe_args)
        except Exception as exc:  # noqa: BLE001 — ANY transport fault must stay
            # inside the governed path (Phase 12 hardening): count it against the
            # breaker and return a refusal, never crash the caller. TransportError
            # and unexpected faults (network stack, decode bugs) behave the same.
            server.health = self._breaker.on_failure(server.health, error=str(exc))
            await self._store.upsert(server)
            return ToolResult(
                server=server_name,
                tool=tool_name,
                ok=False,
                reason="transport_error",
                error=str(exc)[:200],
            )
        latency_ms = (self._now() - start) * 1000.0
        server.health = self._breaker.on_success(server.health, latency_ms=latency_ms)
        await self._store.upsert(server)

        result = ToolResult(
            server=server_name,
            tool=tool_name,
            ok=True,
            output=wrap_untrusted(raw),  # SEC-06: output is data, not instructions
            latency_ms=latency_ms,
        )
        if spec.cacheable:
            self._cache[cache_key] = result
        return result

    # --- helpers -------------------------------------------------------------
    async def _require(self, tenant_id: UUID, name: str) -> RegisteredServer:
        server = await self._store.get(tenant_id, name)
        if server is None:
            raise KeyError(f"server '{name}' is not registered for this tenant")
        return server

    def _resolve_transport(self, tenant_id: UUID, config: ToolServerConfig) -> ToolTransport:
        key = (tenant_id, config.name)
        cached = self._transports.get(key)
        if cached is not None:
            return cached
        credential = self._resolve_credential(config)
        transport = self._provider(config, credential)
        self._transports[key] = transport
        return transport

    def _resolve_credential(self, config: ToolServerConfig) -> str | None:
        """Resolve the connector credential from the vault (SEC-05). The value is
        passed to the transport and never returned to callers or logged."""
        if not config.secret_ref or self._secrets is None:
            return None
        get = getattr(self._secrets, "get", None)
        return get(config.secret_ref) if callable(get) else None

    @staticmethod
    def _apply_default_permissions(
        config: ToolServerConfig, tools: list[ToolSpec]
    ) -> list[ToolSpec]:
        """A tool that declared no permission of its own inherits the server's
        default (deny-by-default composes: the effect/domains still gate at
        invoke). A tool that declared one keeps it."""
        default = config.default_permission
        return [
            t
            if t.permission.model_dump() != _UNSET_PERM
            else t.model_copy(update={"permission": default})
            for t in tools
        ]

    @staticmethod
    def _cache_key(
        tenant_id: UUID, server: str, tool: str, args: dict[str, object]
    ) -> tuple[UUID, str, str, str]:
        items = ";".join(f"{k}={args[k]!r}" for k in sorted(args))
        return (tenant_id, server, tool, items)


# A freshly-defaulted ToolPermission dump — a tool whose permission equals this
# declared none of its own, so it inherits the server default at registration.
from eadip.mcp.models import ToolPermission  # noqa: E402 — avoids a forward ref

_UNSET_PERM = ToolPermission().model_dump()
