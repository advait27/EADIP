"""Tool integration routes (Phase 8, FR-030..033, US-D1).

The self-serve onboarding surface: register an enterprise system as an MCP server
(declaratively, no code deploy), see it appear in discovery with cached tool
schemas, health-check it, and invoke a tool subject to RBAC. Registration/drain
are governed as `tool`/*/write (admin capability); listing + health are reads;
invocation is authorised against the *tool's own* permission descriptor inside
the manager (deny-by-default), then arg-validated before the connector is called.
Every action is audited.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from eadip.gateway.authz import require
from eadip.gateway.dependencies import AuditLogDep, McpManagerDep, ResidencyGuardDep
from eadip.gateway.models import (
    InvokeToolRequest,
    InvokeToolResponse,
    RegisterServerRequest,
    ServerHealthModel,
    ServerResponse,
    ToolItem,
)
from eadip.mcp.models import RegisteredServer, ToolPermission, ToolServerConfig
from eadip.observability.logging import get_logger
from eadip.security.audit import make_event
from eadip.security.identity import Identity
from eadip.security.rbac import Effect

router = APIRouter(prefix="/v1/tools", tags=["tools"])
log = get_logger(__name__)

# Onboarding a connector is an administrative write on the tool surface.
ToolAdminDep = Annotated[Identity, Depends(require("tool", "*", Effect.WRITE))]
# Reading the registry (list / health) is a read on the run/tool surface.
ToolReaderDep = Annotated[Identity, Depends(require("run", "runs", Effect.READ))]
# Invoking is authorised against the tool's own descriptor by the manager; the
# route only requires the caller be an authenticated run actor.
ToolInvokerDep = Annotated[Identity, Depends(require("run", "runs", Effect.READ))]


def _to_server_response(server: RegisteredServer) -> ServerResponse:
    return ServerResponse(
        id=server.id,
        name=server.config.name,
        description=server.config.description,
        transport=server.config.transport,
        health=ServerHealthModel(
            state=str(server.health.state),
            consecutive_failures=server.health.consecutive_failures,
            last_latency_ms=server.health.last_latency_ms,
            last_error=server.health.last_error,
        ),
        tools=[
            ToolItem(
                name=t.name,
                description=t.description,
                input_schema=t.input_schema,
                effect=str(t.permission.effect),
                domains=list(t.permission.domains),
                acl_tags=list(t.permission.acl_tags),
                cacheable=t.cacheable,
            )
            for t in server.tools
        ],
    )


@router.post("", status_code=201, response_model=ServerResponse)
async def register_server(
    body: RegisterServerRequest,
    identity: ToolAdminDep,
    manager: McpManagerDep,
    audit: AuditLogDep,
) -> ServerResponse:
    """Register (or re-register) an MCP server and discover its tools (US-D1)."""
    config = ToolServerConfig(
        name=body.name,
        description=body.description,
        transport=body.transport,
        endpoint=body.endpoint,
        secret_ref=body.secret_ref,
        default_permission=ToolPermission(
            effect=Effect(body.default_permission.effect),
            domains=tuple(body.default_permission.domains),
            acl_tags=tuple(body.default_permission.acl_tags),
        ),
    )
    server = await manager.register(identity.tenant_id, config)
    await audit.record(
        make_event(
            tenant_id=identity.tenant_id,
            actor=str(identity.user_id),
            action="tool.register",
            detail={"server": body.name, "transport": body.transport, "tools": len(server.tools)},
        )
    )
    log.info("tool.registered", server=body.name, tenant_id=str(identity.tenant_id))
    return _to_server_response(server)


@router.get("", response_model=list[ServerResponse])
async def list_servers(identity: ToolReaderDep, manager: McpManagerDep) -> list[ServerResponse]:
    """Discovery: the tenant's registered servers, their tools, and health."""
    servers = await manager.list_servers(identity.tenant_id)
    return [_to_server_response(s) for s in servers]


@router.post("/{name}/health", response_model=ServerHealthModel)
async def health_check(
    name: str, identity: ToolReaderDep, manager: McpManagerDep, audit: AuditLogDep
) -> ServerHealthModel:
    """Probe a server; advance its circuit-breaker state."""
    try:
        health = await manager.health_check(identity.tenant_id, name)
    except KeyError:
        raise HTTPException(status_code=404, detail="server not registered") from None
    await audit.record(
        make_event(
            tenant_id=identity.tenant_id,
            actor=str(identity.user_id),
            action="tool.health",
            detail={"server": name, "state": str(health.state)},
        )
    )
    return ServerHealthModel(
        state=str(health.state),
        consecutive_failures=health.consecutive_failures,
        last_latency_ms=health.last_latency_ms,
        last_error=health.last_error,
    )


@router.post("/{name}/drain", response_model=ServerResponse)
async def drain_server(
    name: str, identity: ToolAdminDep, manager: McpManagerDep, audit: AuditLogDep
) -> ServerResponse:
    """Quiesce a server for removal (lifecycle DRAINING — no new invocations)."""
    try:
        server = await manager.drain(identity.tenant_id, name)
    except KeyError:
        raise HTTPException(status_code=404, detail="server not registered") from None
    await audit.record(
        make_event(
            tenant_id=identity.tenant_id,
            actor=str(identity.user_id),
            action="tool.drain",
            detail={"server": name},
        )
    )
    return _to_server_response(server)


@router.post("/{name}/{tool}/invoke", response_model=InvokeToolResponse)
async def invoke_tool(
    name: str,
    tool: str,
    body: InvokeToolRequest,
    identity: ToolInvokerDep,
    _residency: ResidencyGuardDep,
    manager: McpManagerDep,
    audit: AuditLogDep,
) -> InvokeToolResponse:
    """Invoke a tool. The manager does the pre-invocation RBAC check against the
    tool's descriptor, validates arguments, and guards with the circuit breaker;
    a governance refusal returns ok=false with a reason (403 for RBAC denial)."""
    result = await manager.invoke(identity, name, tool, body.arguments)
    await audit.record(
        make_event(
            tenant_id=identity.tenant_id,
            actor=str(identity.user_id),
            action="tool.invoke",
            detail={"server": name, "tool": tool, "ok": result.ok, "reason": result.reason},
        )
    )
    if not result.ok and result.reason == "unknown_server":
        raise HTTPException(status_code=404, detail="server not registered")
    if not result.ok and result.reason == "unknown_tool":
        raise HTTPException(status_code=404, detail="tool not found")
    if not result.ok and result.reason == "rbac_denied":
        raise HTTPException(status_code=403, detail="forbidden")
    return InvokeToolResponse(
        server=result.server,
        tool=result.tool,
        ok=result.ok,
        output=result.output,
        error=result.error,
        reason=result.reason,
        latency_ms=result.latency_ms,
        from_cache=result.from_cache,
    )
