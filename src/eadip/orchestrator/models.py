"""Typed I/O contracts for the orchestration agents (TAD Ch 5, AP-6).

Every agent has a typed input/output so the graph is statically reasoned about
and the prompt contracts (for the LLM variants) have a schema to fill. Pydantic
so the same models serialise into the checkpoint and over SSE.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from eadip.retrieval.text import jaccard, tokens


class Goal(BaseModel):
    """Goal Interpreter output: the structured intent behind the question."""

    objective: str
    metrics: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)  # regions/segments/products
    time_range: str | None = None
    complexity: str = "standard"  # "simple" | "standard" | "deep"


class PlanStep(BaseModel):
    id: str
    kind: str  # "retrieve" | "analyze" | "tool"
    description: str
    params: dict = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    status: str = "pending"  # "pending" | "running" | "done" | "failed" | "skipped"


class Plan(BaseModel):
    steps: list[PlanStep] = Field(default_factory=list)
    rationale: str = ""

    def signature(self) -> str:
        """A stable text signature of the plan, used for loop detection."""
        return " | ".join(f"{s.kind}:{s.description}" for s in self.steps)

    def similarity(self, other: str) -> float:
        return jaccard(set(tokens(self.signature())), set(tokens(other)))


class Evidence(BaseModel):
    kind: str  # "passage" | "query" | "tool"
    ref: str  # source_ref, the exact SQL, or server.tool — the provenance pointer (FR-024)
    snippet: str = ""


class Finding(BaseModel):
    claim: str
    source: str  # "retrieval" | "analytics" | "tool"
    step_id: str = ""
    kind: str = ""  # "headline"|"driver"|"anomaly"|"forecast"|"correlation"|"passage"|"tool_result"
    magnitude: float | None = None
    association_only: bool = False  # correlation != causation, carried through
    evidence: list[Evidence] = Field(default_factory=list)
    detail: dict = Field(default_factory=dict)  # structured params for re-derivation (Phase 7)


class StepResult(BaseModel):
    step_id: str
    kind: str
    ok: bool
    summary: str = ""
    findings: list[Finding] = Field(default_factory=list)
    cost_usd: float = 0.0
    attempts: int = 1
    error: str | None = None


class Reflection(BaseModel):
    """Reflection agent output: is the evidence sufficient, or must we re-plan?"""

    sufficient: bool
    gaps: list[str] = Field(default_factory=list)
    should_replan: bool = False
    rationale: str = ""


class Event(BaseModel):
    """A streamed progress event (SSE). ``type`` drives the SSE event name."""

    type: str
    data: dict = Field(default_factory=dict)
