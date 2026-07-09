"""Retrieval orchestration (TAD Ch 7.1).

expand -> embed sub-queries (dense) + sparse-encode -> per-sub-query dense+sparse
search (ACL/time filtered in the store, pre-ANN) -> RRF fuse -> merge GraphRAG
lineage evidence (deep effort) -> rerank -> context compression -> ranked
evidence. Adaptive effort: "simple" skips expansion and reranking; "deep" adds
graph-augmented retrieval (GraphRAG, Phase 10) so structural "why" questions are
grounded in explicit metric lineage, not just vector similarity.
"""

from __future__ import annotations

from eadip.graph.service import GraphRAGService
from eadip.ports.embeddings import Embedder, SparseEncoder
from eadip.ports.vector_store import ScoredPoint, SearchFilter, VectorStore
from eadip.retrieval.compression import compress_passages
from eadip.retrieval.fusion import reciprocal_rank_fusion
from eadip.retrieval.models import Passage, Query, RetrievalResult
from eadip.retrieval.multi_query import QueryExpander
from eadip.retrieval.rerank import Reranker


class RetrievalService:
    def __init__(
        self,
        *,
        embedder: Embedder,
        sparse_encoder: SparseEncoder,
        vector_store: VectorStore,
        expander: QueryExpander,
        reranker: Reranker,
        rrf_k: int = 60,
        candidate_k: int = 40,
        context_char_budget: int = 4000,
        rerank_enabled: bool = True,
        multi_query_enabled: bool = True,
        graph: GraphRAGService | None = None,
    ) -> None:
        self._embedder = embedder
        self._sparse = sparse_encoder
        self._vs = vector_store
        self._expander = expander
        self._reranker = reranker
        self._rrf_k = rrf_k
        self._candidate_k = candidate_k
        self._budget = context_char_budget
        self._rerank_enabled = rerank_enabled
        self._multi_query = multi_query_enabled
        self._graph = graph

    async def retrieve(self, query: Query) -> RetrievalResult:
        sub_queries = [query.text]
        if self._multi_query and query.effort != "simple":
            sub_queries = await self._expander.expand(query.text)

        flt = SearchFilter(acl_tags=query.acl_tags, sources=query.sources, as_of=query.as_of)
        rankings: list[list[ScoredPoint]] = []
        for sub in sub_queries:
            dense = (await self._embedder.embed([sub]))[0]
            sparse = self._sparse.encode([sub])[0]
            rankings.append(
                await self._vs.search_dense(
                    query.tenant_id, vector=dense, flt=flt, limit=self._candidate_k
                )
            )
            rankings.append(
                await self._vs.search_sparse(
                    query.tenant_id, vector=sparse, flt=flt, limit=self._candidate_k
                )
            )

        fused = reciprocal_rank_fusion(rankings, k=self._rrf_k)
        passages = [self._to_passage(p) for p in fused[: self._candidate_k]]

        # GraphRAG (deep effort): merge metric-lineage evidence with vector hits.
        # Graph evidence leads (it directly answers structural "why") and is
        # de-duplicated against vector passages by source_ref.
        graph_evidence: list[Passage] = []
        if self._graph is not None and query.effort == "deep":
            graph_evidence = await self._graph.evidence(
                query.tenant_id, query.text, acl_tags=query.acl_tags
            )
            passages = self._merge_graph(graph_evidence, passages)

        if self._rerank_enabled and query.effort != "simple":
            passages = self._reranker.rerank(query.text, passages)
        # Keep graph evidence from being ranked out: it is prepended post-rerank.
        passages = self._merge_graph(graph_evidence, passages)[: query.top_k]
        passages = compress_passages(passages, query.text, char_budget=self._budget)

        return RetrievalResult(
            passages=passages,
            sub_queries=sub_queries,
            candidates_considered=len(fused) + len(graph_evidence),
        )

    @staticmethod
    def _merge_graph(graph_evidence: list[Passage], passages: list[Passage]) -> list[Passage]:
        """Prepend graph evidence (dedup by id), preserving order of the rest."""
        if not graph_evidence:
            return passages
        graph_ids = {g.id for g in graph_evidence}
        rest = [p for p in passages if p.id not in graph_ids]
        return list(graph_evidence) + rest

    @staticmethod
    def _to_passage(point: ScoredPoint) -> Passage:
        payload = point.payload
        return Passage(
            id=point.id,
            text=str(payload.get("text", "")),
            score=point.score,
            source_ref=str(payload.get("source_ref", "")),
            source_type=str(payload.get("source_type", "")),
            document_id=payload.get("document_id"),
            ordinal=payload.get("ordinal"),
            acl_tags=tuple(payload.get("acl_tags", [])),
        )
