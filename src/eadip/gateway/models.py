"""API-boundary Pydantic models (TAD Appendix A.1)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from eadip.domain.entities import RunStatus


class RunContext(BaseModel):
    time_range: str | None = None
    scope: list[str] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=list)


class CreateRun(BaseModel):
    question: str = Field(min_length=1, description="Natural-language business question (FR-001)")
    context: RunContext | None = None


class RunResponse(BaseModel):
    id: UUID
    status: RunStatus
    question: str
    events_url: str


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, description="Natural-language search query (FR-010)")
    sources: list[str] = Field(default_factory=list)
    as_of: datetime | None = None
    k: int | None = Field(default=None, ge=1, le=50)
    effort: str = Field(default="standard", pattern="^(simple|standard|deep)$")


class EvidenceItem(BaseModel):
    id: UUID
    text: str
    score: float
    source_ref: str
    source_type: str


class SearchResponse(BaseModel):
    query: str
    sub_queries: list[str]
    candidates_considered: int
    evidence: list[EvidenceItem]


class AnalyzeRequest(BaseModel):
    question: str = Field(min_length=1, description="Metric-movement question (FR-020)")
    metric: str | None = Field(default=None, description="Logical metric, e.g. gross_margin")
    dimension: str | None = Field(default=None, description="Driver dimension, e.g. product_line")
    filter_column: str = "region"
    filter_value: str | None = Field(default=None, description="Equality filter, e.g. EMEA")
    period_column: str = "period"
    baseline_period: str | None = None
    current_period: str | None = None
    effort: str = Field(default="standard", pattern="^(simple|standard|deep)$")


class DriverItem(BaseModel):
    label: str
    baseline: float
    current: float
    delta: float
    share: float


class FindingItem(BaseModel):
    kind: str
    statement: str
    magnitude: float | None = None
    evidence_sql: str  # provenance: the exact query behind this claim (FR-024)
    association_only: bool = False
    detail: dict = Field(default_factory=dict)


class QueryItem(BaseModel):
    purpose: str
    sql: str
    row_count: int
    truncated: bool
    attempts: int


class AnalyzeResponse(BaseModel):
    question: str
    metric: str
    headline: str
    verified: bool
    drivers: list[DriverItem]
    findings: list[FindingItem]
    queries: list[QueryItem]
    warnings: list[str]


class ClaimCounts(BaseModel):
    verified: int = 0
    unverified: int = 0
    conflicting: int = 0


class RunStatusResponse(BaseModel):
    """Where a run is right now (Glass Box): enough for a UI to render after a
    refresh without opening the stream."""

    id: UUID
    question: str
    status: str
    running: bool  # executing in this process right now
    iterations: int
    cost_usd: float
    elapsed_s: float
    stop_reason: str | None = None
    findings: int
    claims: ClaimCounts
    pending_approvals: int
    has_brief: bool
    last_seq: int  # head of the event log (use as Last-Event-ID)


class TimelineResponse(BaseModel):
    run_id: UUID
    question: str
    status: str
    events: list[dict]  # serialised LoggedEvent: seq, at, type, data


class ShareResponse(BaseModel):
    token: str
    url: str  # the replay page (UI when built, else the API payload)
    api_url: str
    expires_at: datetime


class SharePayload(BaseModel):
    """A read-only replay of a run for anyone holding the link."""

    run_id: UUID
    question: str
    status: str
    expires_at: datetime
    timeline: list[dict]
    graph: dict
    brief: dict | None = None
    evidence_bundle_url: str


class ReportResponse(BaseModel):
    """The verified executive brief for a run (Phase 7, EXP-01..06)."""

    run_id: UUID
    status: str
    verified: int
    unverified: int
    conflicting: int
    brief: dict  # the serialised ExecutiveBrief (headline → findings → recs → drill-down)


# --- MCP integration & tool calling (Phase 8, FR-030..033) -------------------
class ToolPermissionModel(BaseModel):
    """Declarative permission descriptor on a registered tool."""

    effect: str = "read"  # "read" | "write" | "high_impact"
    domains: list[str] = Field(default_factory=lambda: ["*"])
    acl_tags: list[str] = Field(default_factory=list)


class RegisterServerRequest(BaseModel):
    """Onboard an enterprise system as an MCP server (US-D1) — no code deploy.
    `transport` defaults to the in-process demo path; http/stdio name a real
    endpoint whose credential is resolved from the vault via `secret_ref`."""

    name: str = Field(min_length=1, max_length=64, description="Unique server name for this tenant")
    description: str = ""
    transport: str = Field(default="inprocess", pattern="^(inprocess|http|stdio)$")
    endpoint: str = ""
    secret_ref: str = ""  # vault key — never the credential value (SEC-05)
    default_permission: ToolPermissionModel = Field(default_factory=ToolPermissionModel)


class ToolItem(BaseModel):
    name: str
    description: str
    input_schema: dict = Field(default_factory=dict)
    effect: str
    domains: list[str]
    acl_tags: list[str]
    cacheable: bool


class ServerHealthModel(BaseModel):
    state: str
    consecutive_failures: int
    last_latency_ms: float
    last_error: str


class ServerResponse(BaseModel):
    """A registered MCP server with its discovered tools and live health."""

    id: UUID
    name: str
    description: str
    transport: str
    health: ServerHealthModel
    tools: list[ToolItem]


class InvokeToolRequest(BaseModel):
    arguments: dict = Field(default_factory=dict)


class InvokeToolResponse(BaseModel):
    """The outcome of a governed tool invocation. `output` is untrusted tool data
    (SEC-06), never instructions; `reason` explains a governance/transport refusal."""

    server: str
    tool: str
    ok: bool
    output: object | None = None
    error: str = ""
    reason: str = ""
    latency_ms: float = 0.0
    from_cache: bool = False


# --- Governed autonomy — human approval (Phase 9, FR-034) --------------------
class ApprovalItem(BaseModel):
    """A pending approval: the exact proposed action awaiting a human ruling."""

    id: UUID
    status: str
    step_id: str
    kind: str
    effect: str
    server: str
    tool: str
    summary: str
    payload: dict


class DecisionRequest(BaseModel):
    """A human's ruling on a pending approval (US-C1): approve / reject / edit."""

    decision: str = Field(pattern="^(approve|reject|edit)$")
    reason: str = ""
    edited_payload: dict | None = None  # required for 'edit' — the amended arguments


class DecisionResponse(BaseModel):
    approval_id: UUID
    status: str
    step_id: str
    decided_by: str
    resume_url: str  # re-open the SSE stream here to continue the run


# --- Administration Portal (Phase 11, FR-052/053/056/057) ---------------------
class RegisterPromptRequest(BaseModel):
    """Register the next immutable version of a named prompt (US-D2)."""

    name: str = Field(min_length=1, max_length=128)
    content: str = Field(min_length=1)


class RecordPromptEvalRequest(BaseModel):
    """Attach an eval-harness score to a version — the promotion gate's evidence."""

    score: float = Field(ge=0.0, le=1.0)
    source: str = "eadip-eval"


class CanaryRequest(BaseModel):
    fraction: float = Field(gt=0.0, le=0.5, description="Share of traffic for the candidate")


class PromptVersionItem(BaseModel):
    name: str
    version: int
    stage: str
    content_sha: str
    eval_score: float | None = None
    eval_source: str = ""
    canary_fraction: float = 0.0
    created_by: str = ""


class RoutingItem(BaseModel):
    task: str
    tier: str
    model: str
    pinned: bool
    reason: str


class SetRoutingRequest(BaseModel):
    tier: str = Field(pattern="^(strong|standard|light)$")


class BudgetItem(BaseModel):
    tenant_id: UUID
    monthly_cap_usd: float
    spent_usd: float
    exhausted: bool


class SetBudgetRequest(BaseModel):
    monthly_cap_usd: float = Field(ge=0.0)


class SetFlagRequest(BaseModel):
    enabled: bool


class RoleItem(BaseModel):
    name: str
    permissions: list[str]  # "resource/domain/effect" triples


class CreateRoleRequest(BaseModel):
    """Define a custom role from explicit grants (deny-by-default: a role only
    ever adds permissions). Built-in roles cannot be redefined."""

    name: str = Field(min_length=1, max_length=64)
    permissions: list[str] = Field(
        min_length=1, description='Grants as "resource/domain/effect" triples'
    )


class MemoryStatusItem(BaseModel):
    tenant_id: UUID
    episodic: int
    semantic: int


class ErasureResponse(BaseModel):
    tenant_id: UUID
    episodic_erased: int
    semantic_erased: int


class NotificationItem(BaseModel):
    id: UUID
    kind: str
    severity: str
    title: str
    body: str
    run_id: UUID | None = None
    delivered: bool


# --- data residency (Phase 12, PRD §21) ----------------------------------------
class ResidencyItem(BaseModel):
    tenant_id: UUID
    region: str | None  # None = unpinned (served by any region)
    deployment_region: str


class SetResidencyRequest(BaseModel):
    region: str = Field(min_length=1, max_length=64, description="Region to pin the tenant to")
