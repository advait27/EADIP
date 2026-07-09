"""Memory value objects (Phase 11, FR-045 full, TAD Ch 10).

Three tiers, per the TAD memory architecture:
  - working:  scratch state for an in-flight run (the checkpoint already covers
              this; represented here for completeness/consolidation).
  - episodic: per-tenant record of past runs — question, the plan that answered
              it, and the outcome — so a repeat question can reuse a prior plan.
  - semantic: durable, consolidated facts shared across runs. Governed: NEVER
              stores PII (redacted on write) and is erasure-cascaded per tenant.

Pure Pydantic — no IO. The Memory Agent consolidates a finished run into these.
"""

from __future__ import annotations

from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class MemoryTier(StrEnum):
    WORKING = "working"
    EPISODIC = "episodic"
    SEMANTIC = "semantic"


class EpisodicMemory(BaseModel):
    """A past run distilled to what makes the next similar question faster: the
    question, the plan signature + steps that answered it, and the outcome."""

    id: UUID = Field(default_factory=uuid4)
    tenant_id: UUID
    question: str
    question_key: str  # normalised key for lookup (content tokens, sorted)
    plan_signature: str
    plan_steps: list[dict] = Field(default_factory=list)  # serialised PlanSteps to replay
    headline: str = ""
    status: str = ""
    created_at_s: float = 0.0
    hits: int = 0  # times this episode was reused (usefulness signal)


class SemanticFact(BaseModel):
    """A durable, PII-free fact consolidated from runs and shared across them.
    `subject`/`predicate`/`object` is a light triple; `confidence` carries the
    verification confidence of the claim it came from."""

    id: UUID = Field(default_factory=uuid4)
    tenant_id: UUID
    subject: str
    predicate: str
    object: str
    confidence: float = 0.0
    source_run_id: UUID | None = None
    created_at_s: float = 0.0

    def statement(self) -> str:
        return f"{self.subject} {self.predicate} {self.object}".strip()


class ConsolidationResult(BaseModel):
    """What the Memory Agent wrote when consolidating a run."""

    episodic_written: int = 0
    semantic_written: int = 0
    semantic_skipped_pii: int = 0  # facts dropped because they carried PII (governance)
