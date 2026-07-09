"""End-to-end retrieval over an ingested corpus (Phase 4 DoD)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from eadip.adapters.hash_embedder import HashEmbedder
from eadip.adapters.memory_embedding_cache import InMemoryEmbeddingCache
from eadip.adapters.memory_vector_store import InMemoryVectorStore
from eadip.adapters.sparse_encoder import HashingSparseEncoder
from eadip.ingestion.models import IngestRequest
from eadip.ingestion.pipeline import DocumentPipeline
from eadip.retrieval.models import Query
from eadip.retrieval.multi_query import HeuristicQueryExpander
from eadip.retrieval.rerank import LexicalReranker
from eadip.retrieval.service import RetrievalService


def _pipeline(vs: InMemoryVectorStore) -> DocumentPipeline:
    return DocumentPipeline(
        embedder=HashEmbedder(dimension=128),
        sparse_encoder=HashingSparseEncoder(),
        cache=InMemoryEmbeddingCache(),
        vector_store=vs,
        chunk_max_chars=400,
        chunk_overlap=40,
    )


def _service(vs: InMemoryVectorStore) -> RetrievalService:
    return RetrievalService(
        embedder=HashEmbedder(dimension=128),
        sparse_encoder=HashingSparseEncoder(),
        vector_store=vs,
        expander=HeuristicQueryExpander(),
        reranker=LexicalReranker(),
        candidate_k=20,
        context_char_budget=2000,
    )


async def _ingest(pipe: DocumentPipeline, tenant: UUID, text: str, **kw: object) -> None:
    await pipe.ingest(
        IngestRequest(
            tenant_id=tenant,
            raw=text.encode(),
            content_type="text/plain",
            source_ref=str(kw.pop("source_ref", "r")),
            **kw,  # type: ignore[arg-type]
        )
    )


async def test_grounded_retrieval_ranks_relevant_chunk_first() -> None:
    vs = InMemoryVectorStore()
    tenant = uuid4()
    pipe, svc = _pipeline(vs), _service(vs)
    await _ingest(
        pipe,
        tenant,
        "EMEA gross margin fell three points driven by unfavorable FX and higher "
        "COGS from a supplier price increase.",
        source_ref="finance/emea",
    )
    await _ingest(
        pipe,
        tenant,
        "The company picnic is in July near the lake. Bring sunscreen and hats.",
        source_ref="hr/picnic",
    )

    result = await svc.retrieve(Query(tenant_id=tenant, text="why did EMEA margin fall?"))
    assert result.passages
    assert "margin" in result.passages[0].text.lower()
    assert len(result.sub_queries) >= 1


async def test_restricted_docs_never_appear_for_unauthorized_user() -> None:
    vs = InMemoryVectorStore()
    tenant = uuid4()
    pipe, svc = _pipeline(vs), _service(vs)
    await _ingest(pipe, tenant, "Public EMEA margin commentary about FX and COGS.", acl_tags=())
    await _ingest(
        pipe, tenant, "CONFIDENTIAL EMEA margin board memo about FX and COGS.", acl_tags=("board",)
    )

    unauthorized = await svc.retrieve(
        Query(tenant_id=tenant, text="EMEA margin FX COGS", acl_tags=("finance",))
    )
    assert unauthorized.passages
    assert all("CONFIDENTIAL" not in p.text for p in unauthorized.passages)

    authorized = await svc.retrieve(
        Query(tenant_id=tenant, text="EMEA margin FX COGS", acl_tags=("finance", "board"))
    )
    assert any("CONFIDENTIAL" in p.text for p in authorized.passages)


async def test_time_aware_retrieval_returns_point_in_time_truth() -> None:
    vs = InMemoryVectorStore()
    tenant = uuid4()
    pipe, svc = _pipeline(vs), _service(vs)
    await _ingest(
        pipe,
        tenant,
        "Margin definition v1 under the old policy.",
        valid_from=datetime(2025, 1, 1, tzinfo=UTC),
        valid_to=datetime(2025, 12, 31, tzinfo=UTC),
        source_ref="policy/old",
    )
    await _ingest(
        pipe,
        tenant,
        "Margin definition v2 under the current policy.",
        valid_from=datetime(2026, 1, 1, tzinfo=UTC),
        source_ref="policy/current",
    )

    result = await svc.retrieve(
        Query(
            tenant_id=tenant,
            text="margin definition policy",
            as_of=datetime(2026, 6, 1, tzinfo=UTC),
        )
    )
    text = " ".join(p.text for p in result.passages)
    assert "v2" in text
    assert "v1" not in text
