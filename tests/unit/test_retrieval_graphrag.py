"""GraphRAG merged into retrieval (Phase 10): deep effort merges lineage evidence
with vector hits (graph leads, deduped); simple/standard effort does not."""

from __future__ import annotations

from uuid import UUID, uuid4

from eadip.adapters.hash_embedder import HashEmbedder
from eadip.adapters.memory_embedding_cache import InMemoryEmbeddingCache
from eadip.adapters.memory_graph import InMemoryKnowledgeGraph
from eadip.adapters.memory_vector_store import InMemoryVectorStore
from eadip.adapters.sparse_encoder import HashingSparseEncoder
from eadip.graph.meta_model import DEMO_EDGES, DEMO_NODES
from eadip.graph.service import GraphRAGService
from eadip.ingestion.models import IngestRequest
from eadip.ingestion.pipeline import DocumentPipeline
from eadip.retrieval.models import Query
from eadip.retrieval.multi_query import HeuristicQueryExpander
from eadip.retrieval.rerank import LexicalReranker
from eadip.retrieval.service import RetrievalService


async def _graph(tenant: UUID) -> GraphRAGService:
    g = InMemoryKnowledgeGraph()
    await g.upsert_lineage(tenant, list(DEMO_NODES), list(DEMO_EDGES))
    return GraphRAGService(g, hops=2)


def _service(vs: InMemoryVectorStore, graph: GraphRAGService | None) -> RetrievalService:
    return RetrievalService(
        embedder=HashEmbedder(dimension=128),
        sparse_encoder=HashingSparseEncoder(),
        vector_store=vs,
        expander=HeuristicQueryExpander(),
        reranker=LexicalReranker(),
        candidate_k=20,
        context_char_budget=2000,
        graph=graph,
    )


async def _ingest(vs: InMemoryVectorStore, tenant: UUID, text: str, ref: str) -> None:
    pipe = DocumentPipeline(
        embedder=HashEmbedder(dimension=128),
        sparse_encoder=HashingSparseEncoder(),
        cache=InMemoryEmbeddingCache(),
        vector_store=vs,
        chunk_max_chars=400,
        chunk_overlap=40,
    )
    await pipe.ingest(
        IngestRequest(
            tenant_id=tenant, raw=text.encode(), content_type="text/plain", source_ref=ref
        )
    )


async def test_deep_effort_merges_graph_evidence_first() -> None:
    vs = InMemoryVectorStore()
    tenant = uuid4()
    await _ingest(vs, tenant, "EMEA margin commentary and supplier notes.", "finance/emea")
    svc = _service(vs, await _graph(tenant))
    res = await svc.retrieve(
        Query(tenant_id=tenant, text="why did gross margin fall?", effort="deep", top_k=8)
    )
    assert res.passages
    assert res.passages[0].source_type == "graph"  # lineage leads
    assert any(p.source_type == "graph" and "COGS spike" in p.text for p in res.passages)


async def test_standard_effort_has_no_graph_evidence() -> None:
    vs = InMemoryVectorStore()
    tenant = uuid4()
    await _ingest(vs, tenant, "EMEA margin commentary.", "finance/emea")
    svc = _service(vs, await _graph(tenant))
    res = await svc.retrieve(
        Query(tenant_id=tenant, text="why did gross margin fall?", effort="standard", top_k=8)
    )
    assert all(p.source_type != "graph" for p in res.passages)


async def test_no_graph_service_is_a_noop() -> None:
    vs = InMemoryVectorStore()
    tenant = uuid4()
    await _ingest(vs, tenant, "EMEA margin commentary.", "finance/emea")
    svc = _service(vs, None)
    res = await svc.retrieve(
        Query(tenant_id=tenant, text="why did gross margin fall?", effort="deep", top_k=8)
    )
    assert all(p.source_type != "graph" for p in res.passages)
