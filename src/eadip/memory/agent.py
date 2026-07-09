"""Memory Agent (Phase 11, FR-045, TAD Ch 10): consolidate a finished run into
long-term memory, and recall a prior plan for a repeat question.

Consolidation writes two tiers:
  - episodic: the question + the plan that answered it (so a repeat question
    reuses the plan instead of re-planning from scratch).
  - semantic: verified, non-association claims distilled to PII-free facts shared
    across runs. **Governance:** any candidate fact whose text contains PII is
    dropped (never written to shared semantic memory), and `forget(tenant)`
    cascades an erasure across both tiers.

Recall normalises the question to a content-token key and returns a matching
episode; the orchestrator replays its plan (bounded, still re-verified).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING
from uuid import UUID

from eadip.ingestion.pii import PiiRedactor
from eadip.memory.models import ConsolidationResult, EpisodicMemory, SemanticFact
from eadip.memory.ports import EpisodicStore, SemanticStore
from eadip.retrieval.text import content_tokens
from eadip.verification.models import VerificationStatus

if TYPE_CHECKING:
    from eadip.orchestrator.state import RunState

# A run must have concluded usefully before we memorise its plan as reusable.
_REUSABLE_STATUSES = {"done"}


def question_key(question: str) -> str:
    """Normalise a question to a stable lookup key: sorted, de-duplicated content
    tokens. Two phrasings of the same ask collapse to the same key."""
    return " ".join(sorted(set(content_tokens(question))))


class MemoryAgent:
    def __init__(
        self,
        *,
        episodic: EpisodicStore,
        semantic: SemanticStore,
        redactor: PiiRedactor | None = None,
        min_fact_confidence: float = 0.6,
        now: Callable[[], float] | None = None,
    ) -> None:
        self._episodic = episodic
        self._semantic = semantic
        self._redactor = redactor or PiiRedactor()
        self._min_conf = min_fact_confidence
        self._now = now or (lambda: 0.0)

    async def recall_plan(self, tenant_id: UUID, question: str) -> EpisodicMemory | None:
        """A prior episode answering this (normalised) question, or None."""
        episode = await self._episodic.find(tenant_id, question_key(question))
        if episode is not None:
            await self._episodic.touch(tenant_id, episode.id)
        return episode

    async def consolidate(self, state: RunState) -> ConsolidationResult:
        """Distil a finished RunState into episodic + semantic memory. The import
        is TYPE_CHECKING-only (no runtime state->memory cycle)."""
        result = ConsolidationResult()
        status = str(state.status)
        plan = state.plan
        tenant_id: UUID = state.tenant_id
        question: str = state.question

        # Episodic: memorise the plan only for a cleanly-finished run.
        if status.endswith("done") and plan is not None and plan.steps:
            await self._episodic.add(
                EpisodicMemory(
                    tenant_id=tenant_id,
                    question=question,
                    question_key=question_key(question),
                    plan_signature=plan.signature(),
                    plan_steps=[s.model_dump() for s in plan.steps],
                    headline=state.brief.headline if state.brief is not None else "",
                    status=status,
                    created_at_s=self._now(),
                )
            )
            result.episodic_written = 1

        # Semantic: verified, non-association claims -> PII-free shared facts.
        for claim in state.verified_claims:
            if claim.status is not VerificationStatus.VERIFIED or claim.association_only:
                continue
            if claim.confidence < self._min_conf:
                continue
            _redacted, found = self._redactor.redact(claim.claim)
            if found:  # governance: never write PII to shared semantic memory
                result.semantic_skipped_pii += 1
                continue
            await self._semantic.add(
                SemanticFact(
                    tenant_id=tenant_id,
                    subject=question_key(question)[:80] or "run",
                    predicate="found",
                    object=claim.claim[:200],
                    confidence=claim.confidence,
                    source_run_id=state.run_id,
                    created_at_s=self._now(),
                )
            )
            result.semantic_written += 1
        return result

    async def status(self, tenant_id: UUID) -> tuple[int, int]:
        """(episodic, semantic) memory counts for a tenant (admin portal)."""
        return (
            await self._episodic.count(tenant_id),
            await self._semantic.count(tenant_id),
        )

    async def facts(self, tenant_id: UUID) -> list[SemanticFact]:
        """The tenant's consolidated semantic facts (admin drill-down)."""
        return await self._semantic.all(tenant_id)

    async def forget_tenant(self, tenant_id: UUID) -> tuple[int, int]:
        """Erasure cascade across both tiers; returns (episodic, semantic) counts."""
        return (
            await self._episodic.forget(tenant_id),
            await self._semantic.forget(tenant_id),
        )
