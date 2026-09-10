"""Build a RetrievalService from settings, reusing the ingestion embedder/store.

The vector store must be the same instance documents were ingested into, so the
caller passes it in (the gateway shares one process-wide store).
"""

from __future__ import annotations

from eadip.config.settings import Settings
from eadip.graph.service import GraphRAGService
from eadip.ingestion.factory import build_embedder, build_sparse_encoder
from eadip.ports.vector_store import VectorStore
from eadip.retrieval.multi_query import HeuristicQueryExpander, QueryExpander
from eadip.retrieval.rerank import LexicalReranker, Reranker
from eadip.retrieval.service import RetrievalService


def _build_expander(settings: Settings) -> QueryExpander:
    if settings.query_expander_backend == "llm":
        from eadip.adapters.litellm_client import LiteLLMClient
        from eadip.platform.factory import build_routing_policy
        from eadip.platform.models import TaskKind
        from eadip.retrieval.multi_query import LLMQueryExpander

        model = build_routing_policy(settings).route(TaskKind.QUERY_EXPAND).model
        return LLMQueryExpander(
            LiteLLMClient(settings.default_model, settings.model_api_base), model=model
        )
    return HeuristicQueryExpander()


def _build_reranker(settings: Settings) -> Reranker:
    if settings.reranker_backend == "cross_encoder":
        from eadip.retrieval.rerank import CrossEncoderReranker

        return CrossEncoderReranker()
    return LexicalReranker()


def build_retrieval_service(
    settings: Settings,
    vector_store: VectorStore,
    *,
    graph: GraphRAGService | None = None,
) -> RetrievalService:
    return RetrievalService(
        embedder=build_embedder(settings),
        sparse_encoder=build_sparse_encoder(),
        vector_store=vector_store,
        expander=_build_expander(settings),
        reranker=_build_reranker(settings),
        rrf_k=settings.rrf_k,
        candidate_k=settings.retrieval_candidate_k,
        context_char_budget=settings.retrieval_context_char_budget,
        rerank_enabled=settings.retrieval_rerank_enabled,
        multi_query_enabled=settings.retrieval_multi_query_enabled,
        graph=graph if settings.graph_enabled else None,
    )
