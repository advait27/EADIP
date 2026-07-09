"""GraphRAG service (Phase 10, FR-013, TAD Ch 7.2).

Answers structural "why" questions from explicit metric lineage:

    detect a seed metric in the question  ->  bounded multi-hop traversal
      ->  relevant-subgraph extraction    ->  linearize to evidence passages

The evidence is returned as `Passage`s (source_type="graph") so the Retriever
merges it with vector hits identically. Tenant- and ACL-scoped throughout. The
seed detector is a deterministic alias match over the graph's metric nodes; an
LLM detector could slot in behind the same `seed_for` seam later.
"""

from __future__ import annotations

from uuid import UUID, uuid5

from eadip.graph.linearize import linearize
from eadip.ports.graph import KnowledgeGraph, Subgraph
from eadip.retrieval.models import Passage

# A stable namespace so a graph passage's id is deterministic per (tenant, line).
_GRAPH_NS = UUID("0a9f1e2c-0000-4000-8000-000000000010")

# Metric aliases the seed detector recognises in free-text questions. Extend as
# the meta-model grows; unknown metrics simply yield no graph evidence.
_METRIC_ALIASES: dict[str, tuple[str, ...]] = {
    "gross_margin": ("margin", "gross margin", "profitability"),
    "revenue": ("revenue", "sales", "topline", "top line"),
    "cogs": ("cogs", "cost of goods", "costs"),
}


class GraphRAGService:
    def __init__(self, graph: KnowledgeGraph, *, hops: int = 2, max_passages: int = 6) -> None:
        self._graph = graph
        self._hops = hops
        self._max = max_passages

    def seed_for(self, question: str) -> str | None:
        """Detect the seed metric a question is about (deterministic alias match).
        Longest alias wins so 'gross margin' beats 'margin'."""
        text = question.lower()
        best: tuple[int, str] | None = None
        for metric, aliases in _METRIC_ALIASES.items():
            for alias in aliases:
                if alias in text and (best is None or len(alias) > best[0]):
                    best = (len(alias), metric)
        return best[1] if best else None

    async def evidence(
        self,
        tenant_id: UUID,
        question: str,
        *,
        acl_tags: tuple[str, ...] = (),
        seed: str | None = None,
    ) -> list[Passage]:
        """Graph evidence for the question: traverse the seed metric's lineage and
        linearize the relevant subgraph. Empty when no metric is recognised or the
        seed is unknown/invisible."""
        seed = seed or self.seed_for(question)
        if seed is None:
            return []
        subgraph = await self._graph.neighborhood(
            tenant_id, seed, hops=self._hops, acl_tags=acl_tags
        )
        return self._to_passages(subgraph)

    def _to_passages(self, subgraph: Subgraph) -> list[Passage]:
        lines = linearize(subgraph)[: self._max]
        # Impact edges are the most decisive "why" evidence; rank them highest so
        # they survive downstream top-k truncation. Score decays by position.
        passages: list[Passage] = []
        n = len(lines)
        for i, line in enumerate(lines):
            passages.append(
                Passage(
                    id=uuid5(_GRAPH_NS, f"{subgraph.seed}:{line}"),
                    text=line,
                    score=1.0 - (i / (n + 1)),
                    source_ref=f"graph:{subgraph.seed}",
                    source_type="graph",
                )
            )
        return passages
