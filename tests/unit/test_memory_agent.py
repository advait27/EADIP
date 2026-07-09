"""Memory Agent (Phase 11, FR-045): consolidation into episodic + semantic tiers,
PII governance on shared facts, plan recall, and the erasure cascade."""

from __future__ import annotations

from uuid import uuid4

from eadip.domain.entities import RunStatus
from eadip.memory.agent import MemoryAgent, question_key
from eadip.memory.ports import InMemoryEpisodicStore, InMemorySemanticStore
from eadip.orchestrator.models import Plan, PlanStep
from eadip.orchestrator.state import RunState
from eadip.verification.models import VerificationStatus, VerifiedClaim


def _agent() -> MemoryAgent:
    return MemoryAgent(episodic=InMemoryEpisodicStore(), semantic=InMemorySemanticStore())


def _claim(text: str, *, confidence: float = 0.9, association: bool = False) -> VerifiedClaim:
    return VerifiedClaim(
        claim=text,
        source="analytics",
        kind="driver",
        status=VerificationStatus.VERIFIED,
        method="recompute",
        confidence=confidence,
        association_only=association,
    )


def _done_state(question: str = "Why did EMEA gross margin fall last quarter?") -> RunState:
    state = RunState(run_id=uuid4(), tenant_id=uuid4(), user_id=uuid4(), question=question)
    state.status = RunStatus.DONE
    state.plan = Plan(
        steps=[PlanStep(id="s1", kind="retrieve", description="ground")], rationale="r"
    )
    return state


def test_question_key_normalises_phrasings() -> None:
    a = question_key("Why did EMEA gross margin fall last quarter?")
    b = question_key("why did the EMEA gross margin FALL last quarter??")
    assert a == b
    assert question_key("completely different question about churn") != a


async def test_consolidate_writes_episode_and_facts() -> None:
    agent = _agent()
    state = _done_state()
    state.verified_claims = [_claim("EMEA hardware COGS drove the margin decline")]
    result = await agent.consolidate(state)
    assert result.episodic_written == 1
    assert result.semantic_written == 1
    episodic, semantic = await agent.status(state.tenant_id)
    assert (episodic, semantic) == (1, 1)


async def test_consolidate_skips_failed_runs_and_weak_or_association_claims() -> None:
    agent = _agent()
    state = _done_state()
    state.status = RunStatus.FAILED
    state.verified_claims = [
        _claim("low confidence", confidence=0.2),
        _claim("association only", association=True),
    ]
    result = await agent.consolidate(state)
    assert result.episodic_written == 0  # failed run -> no reusable plan
    assert result.semantic_written == 0  # weak/association claims not memorised


async def test_pii_never_reaches_shared_semantic_memory() -> None:
    agent = _agent()
    state = _done_state()
    state.verified_claims = [_claim("Contact jane.doe@acme.com about the margin drop")]
    result = await agent.consolidate(state)
    assert result.semantic_written == 0
    assert result.semantic_skipped_pii == 1
    assert await agent.facts(state.tenant_id) == []


async def test_semantic_dedup_makes_consolidation_idempotent() -> None:
    agent = _agent()
    state = _done_state()
    state.verified_claims = [_claim("EMEA hardware COGS drove the margin decline")]
    await agent.consolidate(state)
    await agent.consolidate(state)  # repeat run of the same question
    _, semantic = await agent.status(state.tenant_id)
    assert semantic == 1


async def test_recall_matches_rephrased_question_and_counts_hits() -> None:
    agent = _agent()
    state = _done_state("Why did EMEA gross margin fall last quarter?")
    await agent.consolidate(state)
    episode = await agent.recall_plan(
        state.tenant_id, "WHY did the EMEA gross margin fall last quarter???"
    )
    assert episode is not None and episode.plan_steps
    again = await agent.recall_plan(state.tenant_id, state.question)
    assert again is not None and again.hits >= 1  # usefulness signal


async def test_recall_is_tenant_scoped() -> None:
    agent = _agent()
    state = _done_state()
    await agent.consolidate(state)
    assert await agent.recall_plan(uuid4(), state.question) is None


async def test_erasure_cascade_empties_both_tiers() -> None:
    agent = _agent()
    state = _done_state()
    state.verified_claims = [_claim("EMEA hardware COGS drove the margin decline")]
    await agent.consolidate(state)
    episodic, semantic = await agent.forget_tenant(state.tenant_id)
    assert (episodic, semantic) == (1, 1)
    assert await agent.status(state.tenant_id) == (0, 0)
    assert await agent.recall_plan(state.tenant_id, state.question) is None
