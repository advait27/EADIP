from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from eadip.adapters.hash_embedder import HashEmbedder
from eadip.adapters.memory_document_repository import InMemoryDocumentRepository
from eadip.adapters.memory_embedding_cache import InMemoryEmbeddingCache
from eadip.adapters.memory_vector_store import InMemoryVectorStore
from eadip.adapters.sparse_encoder import HashingSparseEncoder
from eadip.ingestion.models import IngestRequest
from eadip.ingestion.pii import PiiRedactor
from eadip.ingestion.pipeline import DocumentPipeline
from eadip.ports.embeddings import DenseVector


class CountingEmbedder:
    def __init__(self, inner: HashEmbedder) -> None:
        self._inner = inner
        self.calls = 0
        self.model_version = inner.model_version
        self.dimension = inner.dimension

    async def embed(self, texts: list[str]) -> list[DenseVector]:
        self.calls += len(texts)
        return await self._inner.embed(texts)


def _pipeline(
    vs: InMemoryVectorStore,
    cache: InMemoryEmbeddingCache,
    embedder: CountingEmbedder,
    repo: InMemoryDocumentRepository | None = None,
) -> DocumentPipeline:
    return DocumentPipeline(
        embedder=embedder,
        sparse_encoder=HashingSparseEncoder(),
        cache=cache,
        vector_store=vs,
        pii_redactor=PiiRedactor(),
        document_repo=repo,
        chunk_max_chars=120,
        chunk_overlap=20,
    )


async def test_pipeline_redacts_pii_and_attaches_acl_time_payload() -> None:
    vs, cache = InMemoryVectorStore(), InMemoryEmbeddingCache()
    embedder = CountingEmbedder(HashEmbedder(dimension=64))
    repo = InMemoryDocumentRepository()
    tenant = uuid4()
    raw = (
        b"<p>Contact priya@example.com about EMEA margin. "
        b"Revenue fell twelve percent due to FX and COGS.</p>"
    )
    request = IngestRequest(
        tenant_id=tenant,
        raw=raw,
        content_type="text/html",
        source_ref="kb#1",
        acl_tags=("finance", "emea"),
        valid_from=datetime(2026, 1, 1, tzinfo=UTC),
    )

    result = await _pipeline(vs, cache, embedder, repo).ingest(request)

    assert result.chunk_count >= 1
    assert await vs.count(tenant) == result.chunk_count
    assert "email" in result.pii_types_found

    points = vs.points(tenant)
    assert all("priya@example.com" not in p.payload["text"] for p in points)
    assert all(p.payload["acl_tags"] == ["finance", "emea"] for p in points)
    assert all(p.payload["valid_from"] == "2026-01-01T00:00:00+00:00" for p in points)
    assert all(p.dense and p.sparse is not None for p in points)

    assert len(repo.documents) == 1
    assert repo.documents[0].chunk_count == result.chunk_count


async def test_reingesting_identical_content_hits_cache() -> None:
    vs, cache = InMemoryVectorStore(), InMemoryEmbeddingCache()
    embedder = CountingEmbedder(HashEmbedder(dimension=64))
    tenant = uuid4()
    request = IngestRequest(
        tenant_id=tenant,
        raw=b"Revenue fell in EMEA. Margins compressed. FX hurt.",
        content_type="text/plain",
        source_ref="k#1",
    )
    pipeline = _pipeline(vs, cache, embedder)

    first = await pipeline.ingest(request)
    assert first.cache_hits == 0
    calls_after_first = embedder.calls
    assert calls_after_first == first.chunk_count

    second = await pipeline.ingest(request)
    assert second.cache_hits == second.chunk_count
    assert embedder.calls == calls_after_first  # no new model calls
