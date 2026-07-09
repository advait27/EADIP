from __future__ import annotations

from uuid import uuid4

from eadip.ports.vector_store import ScoredPoint
from eadip.retrieval.compression import compress_passages
from eadip.retrieval.fusion import reciprocal_rank_fusion
from eadip.retrieval.models import Passage
from eadip.retrieval.multi_query import HeuristicQueryExpander
from eadip.retrieval.rerank import LexicalReranker


def _sp(score: float) -> ScoredPoint:
    return ScoredPoint(id=uuid4(), score=score, payload={})


def test_rrf_rewards_agreement_across_lists() -> None:
    shared = _sp(0.1)
    a = [shared, _sp(0.2)]
    b = [shared, _sp(0.3)]
    fused = reciprocal_rank_fusion([a, b], k=60)
    # The item appearing top in both lists fuses highest.
    assert fused[0].id == shared.id


async def test_heuristic_expander_adds_keyword_variant() -> None:
    variants = await HeuristicQueryExpander().expand("Why did EMEA margin fall last quarter?")
    assert variants[0] == "Why did EMEA margin fall last quarter?"
    assert any("margin" in v and "why" not in v.lower().split() for v in variants[1:])
    assert len(variants) == len(set(variants))


def test_lexical_reranker_orders_by_overlap() -> None:
    passages = [
        Passage(id=uuid4(), text="completely unrelated text about weather", score=0.9),
        Passage(id=uuid4(), text="EMEA margin fell due to FX and COGS", score=0.1),
    ]
    ranked = LexicalReranker().rerank("why did EMEA margin fall", passages)
    assert "margin" in ranked[0].text


def test_compression_respects_budget() -> None:
    long_text = " ".join(f"Sentence {i} about margin and revenue." for i in range(50))
    passages = [Passage(id=uuid4(), text=long_text, score=1.0)]
    compressed = compress_passages(passages, "margin revenue", char_budget=120)
    assert compressed
    assert sum(len(p.text) for p in compressed) <= 120 + 10  # join spacing slack
