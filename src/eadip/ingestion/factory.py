"""Build ingestion components from settings, selecting backends.

Defaults (hash embedder, in-memory cache + vector store) need no external
services; flip settings to use LiteLLM / Redis / Qdrant in production. The
builders are public so the retrieval factory reuses the same embedder + store.
"""

from __future__ import annotations

from eadip.adapters.hash_embedder import HashEmbedder
from eadip.adapters.memory_embedding_cache import InMemoryEmbeddingCache
from eadip.adapters.memory_vector_store import InMemoryVectorStore
from eadip.adapters.sparse_encoder import HashingSparseEncoder
from eadip.config.settings import Settings
from eadip.ingestion.pii import PiiRedactor
from eadip.ingestion.pipeline import DocumentPipeline
from eadip.ports.embeddings import Embedder, EmbeddingCache, SparseEncoder
from eadip.ports.vector_store import VectorStore
from eadip.security.audit import AuditLog


def build_embedder(settings: Settings) -> Embedder:
    if settings.embedding_backend == "litellm":
        from eadip.adapters.litellm_embedder import LiteLLMEmbedder

        return LiteLLMEmbedder(settings.litellm_embedding_model)
    return HashEmbedder(dimension=settings.hash_embedding_dim)


def build_sparse_encoder() -> SparseEncoder:
    return HashingSparseEncoder()


def build_cache(settings: Settings) -> EmbeddingCache:
    if settings.embedding_cache_backend == "redis":
        from eadip.adapters.redis_embedding_cache import RedisEmbeddingCache

        return RedisEmbeddingCache(settings.redis_url.get_secret_value())
    return InMemoryEmbeddingCache()


def build_vector_store(settings: Settings) -> VectorStore:
    if settings.vector_backend == "qdrant":
        from eadip.adapters.qdrant_store import QdrantVectorStore

        return QdrantVectorStore(settings.qdrant_url, prefix=settings.qdrant_collection_prefix)
    return InMemoryVectorStore(prefix=settings.qdrant_collection_prefix)


def build_pipeline(
    settings: Settings,
    *,
    vector_store: VectorStore | None = None,
    audit: AuditLog | None = None,
) -> DocumentPipeline:
    return DocumentPipeline(
        embedder=build_embedder(settings),
        sparse_encoder=build_sparse_encoder(),
        cache=build_cache(settings),
        vector_store=vector_store or build_vector_store(settings),
        pii_redactor=PiiRedactor() if settings.pii_redaction_enabled else None,
        audit=audit,
        chunk_max_chars=settings.chunk_max_chars,
        chunk_overlap=settings.chunk_overlap,
    )
