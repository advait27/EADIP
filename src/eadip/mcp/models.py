"""MCP integration domain models (Phase 8, TAD Ch 9, FR-030..033).

Every enterprise system is onboarded as an MCP *server* exposing one or more
*tools*. Each tool carries a declarative **permission descriptor** (effect +
data domains + ACL tags) so RBAC can be enforced *before* invocation, and a
JSON-Schema for its arguments so calls are type-checked at the boundary. These
are pure Pydantic/enum types — no IO — shared by the registry, the Tool Agent,
the argument validator and the orchestrator executor.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from eadip.security.rbac import Effect


class ServerState(StrEnum):
    """MCP server lifecycle (TAD Ch 9). Only HEALTHY/DEGRADED are invocable;
    DEGRADED is invocable but de-prioritised by the Tool Agent's tie-break."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    FAILED = "failed"
    DRAINING = "draining"  # being removed / quiesced — no new invocations


class ToolPermission(BaseModel):
    """Declarative per-tool permission descriptor (US-D1). Maps a tool onto the
    RBAC surface: the *effect* it has (read is auto; write/high-impact gate in
    Phase 9), the data *domains* it touches, and ACL *tags* the caller must hold.
    Deny-by-default: an empty catalog grants nothing."""

    effect: Effect = Effect.READ
    domains: tuple[str, ...] = ("*",)  # RBAC data-domains this tool acts on
    acl_tags: tuple[str, ...] = ()  # required ACL tags (subset of caller's tags)

    @property
    def is_side_effecting(self) -> bool:
        return self.effect is not Effect.READ


class ToolSpec(BaseModel):
    """A discovered tool: its identity, a natural-language capability description
    (used for capability matching), the JSON-Schema for its arguments (cached at
    discovery), and its permission descriptor. Read-only tools are cacheable."""

    name: str
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)  # JSON-Schema (cached)
    permission: ToolPermission = Field(default_factory=ToolPermission)
    idempotent: bool = True  # read-only tools are safe to cache/replay

    @property
    def cacheable(self) -> bool:
        return self.idempotent and not self.permission.is_side_effecting


class ToolServerConfig(BaseModel):
    """Declarative server registration (no code deploy — US-D1). `transport`
    selects the adapter; `endpoint`/`command` locate it; `secret_ref` points at a
    vault key (SEC-05 — credentials never inline). Defaults describe an in-process
    server, so the deterministic tests and the demo need no external process."""

    name: str
    description: str = ""
    transport: str = "inprocess"  # "inprocess" | "http" | "stdio"
    endpoint: str = ""  # http url / stdio command target
    secret_ref: str = ""  # vault key for the connector credential (never the value)
    default_permission: ToolPermission = Field(default_factory=ToolPermission)
    health_interval_s: float = 30.0
    weight_latency: float = 1.0  # tie-break weighting (Tool Agent)
    weight_cost: float = 1.0


class ServerHealth(BaseModel):
    """Rolling health of a registered server, driven by the circuit breaker."""

    state: ServerState = ServerState.HEALTHY
    consecutive_failures: int = 0
    last_latency_ms: float = 0.0
    last_error: str = ""
    open_until: float = 0.0  # monotonic time the breaker re-closes (0 = closed)

    @property
    def invocable(self) -> bool:
        return self.state in (ServerState.HEALTHY, ServerState.DEGRADED)


class RegisteredServer(BaseModel):
    """A server as the registry holds it: config + discovered tools + live health.
    Tenant-scoped (registration is per tenant; RLS enforces isolation in PG)."""

    id: UUID = Field(default_factory=uuid4)
    tenant_id: UUID
    config: ToolServerConfig
    tools: list[ToolSpec] = Field(default_factory=list)
    health: ServerHealth = Field(default_factory=ServerHealth)
    registered_at_s: float = 0.0  # onboarding timestamp (KPI: time-to-onboard)

    def tool(self, name: str) -> ToolSpec | None:
        return next((t for t in self.tools if t.name == name), None)


class ToolInvocation(BaseModel):
    """A request to call a specific tool with (already-validated) arguments."""

    server: str
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolResult(BaseModel):
    """The outcome of an invocation. `output` is UNTRUSTED tool output and must be
    treated strictly as data (SEC-06) — never as instructions. `ok=False` carries
    a governance/transport reason (denied, breaker-open, invalid-args, error)."""

    server: str
    tool: str
    ok: bool
    output: Any = None
    error: str = ""
    reason: str = ""  # governance reason when ok=False (e.g. "rbac_denied")
    latency_ms: float = 0.0
    from_cache: bool = False
    fallback_of: str = ""  # set when this result came from a fallback tool (AP-8)


class ToolValidationError(Exception):
    """Arguments failed the typed-arg gate (deny-by-default). `reason` is safe to
    surface and audit; `detail` locates the offending field."""

    def __init__(self, reason: str, detail: dict[str, str] | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.detail = detail or {}
