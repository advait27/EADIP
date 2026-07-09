"""Document ingestion pipeline (FR-015, FR-016, NFR-08).

parse -> redact PII -> chunk -> embed (cache-first) -> sparse encode -> upsert to
the per-tenant vector store, with each chunk carrying ACL + time payload (AP-5).
Optionally records document lineage to a DocumentRepository.
"""

from __future__ import annotations

from uuid import NAMESPACE_URL, uuid4, uuid5

from eadip.ingestion.chunking import chunk_text
from eadip.ingestion.models import IngestRequest, IngestResult
from eadip.ingestion.parsing import parse
from eadip.ingestion.pii import PiiRedactor
from eadip.observability.logging import get_logger
from eadip.ports.documents import DocumentMeta, DocumentRepository
from eadip.ports.embeddings import Embedder, EmbeddingCache, SparseEncoder, cache_key
from eadip.ports.vector_store import VectorPoint, VectorStore
from eadip.security.audit import AuditLog, make_event

log = get_logger(__name__)


class DocumentPipeline:
    def __init__(
        self,
        *,
        embedder: Embedder,
        sparse_encoder: SparseEncoder,
        cache: EmbeddingCache,
        vector_store: VectorStore,
        pii_redactor: PiiRedactor | None = None,
        document_repo: DocumentRepository | None = None,
        audit: AuditLog | None = None,
        chunk_max_chars: int = 1200,
        chunk_overlap: int = 150,
    ) -> None:
        self._embedder = embedder
        self._sparse = sparse_encoder
        self._cache = cache
        self._vs = vector_store
        self._pii = pii_redactor
        self._doc_repo = document_repo
        self._audit = audit
        self._max_chars = chunk_max_chars
        self._overlap = chunk_overlap

    async def ingest(self, req: IngestRequest) -> IngestResult:
        parsed = parse(req.raw, req.content_type, filename=req.filename)
        text = parsed.text
        pii_types: tuple[str, ...] = ()
        if self._pii is not None:
            text, pii_types = self._pii.redact(text)
        if pii_types and self._audit is not None:
            # PII redaction is itself audited (Phase 12, NFR-08): compliance can
            # evidence WHAT was found and WHERE without the values (types only).
            await self._audit.record(
                make_event(
                    tenant_id=req.tenant_id,
                    actor="ingestion-pipeline",
                    action="pii.redacted",
                    detail={"source_ref": req.source_ref, "pii_types": list(pii_types)},
                )
            )

        chunks = chunk_text(text, max_chars=self._max_chars, overlap=self._overlap)
        document_id = uuid4()
        collection = await self._vs.ensure_collection(
            req.tenant_id, dimension=self._embedder.dimension
        )

        dense, cache_hits = await self._embed_cached([c.text for c in chunks])
        sparse = self._sparse.encode([c.text for c in chunks])

        valid_from = req.valid_from.isoformat() if req.valid_from else None
        valid_to = req.valid_to.isoformat() if req.valid_to else None
        points = [
            VectorPoint(
                id=uuid5(NAMESPACE_URL, f"{document_id}:{chunk.ordinal}"),
                dense=dense[i],
                sparse=sparse[i],
                payload={
                    "tenant_id": str(req.tenant_id),
                    "document_id": str(document_id),
                    "ordinal": chunk.ordinal,
                    "text": chunk.text,
                    "source_type": req.source_type,
                    "source_ref": req.source_ref,
                    "acl_tags": list(req.acl_tags),
                    "valid_from": valid_from,
                    "valid_to": valid_to,
                    "embed_model_version": self._embedder.model_version,
                },
            )
            for i, chunk in enumerate(chunks)
        ]
        if points:
            await self._vs.upsert(req.tenant_id, points)

        if self._doc_repo is not None:
            await self._doc_repo.add(
                DocumentMeta(
                    id=document_id,
                    tenant_id=req.tenant_id,
                    source_ref=req.source_ref,
                    source_type=req.source_type,
                    acl_tags=req.acl_tags,
                    chunk_count=len(chunks),
                    embed_model_version=self._embedder.model_version,
                    valid_from=req.valid_from,
                    valid_to=req.valid_to,
                )
            )

        log.info(
            "ingest.done",
            tenant_id=str(req.tenant_id),
            document_id=str(document_id),
            chunks=len(chunks),
            cache_hits=cache_hits,
            pii_types=list(pii_types),
        )
        return IngestResult(
            document_id=document_id,
            collection=collection,
            chunk_count=len(chunks),
            cache_hits=cache_hits,
            pii_types_found=pii_types,
        )

    async def _embed_cached(self, texts: list[str]) -> tuple[list[list[float]], int]:
        if not texts:
            return [], 0
        keys = [cache_key(self._embedder.model_version, t) for t in texts]
        cached = await self._cache.get_many(keys)
        miss_idx = [i for i, k in enumerate(keys) if k not in cached]
        if miss_idx:
            fresh = await self._embedder.embed([texts[i] for i in miss_idx])
            to_put = {keys[i]: vec for i, vec in zip(miss_idx, fresh, strict=True)}
            await self._cache.put_many(to_put)
            cached.update(to_put)
        vectors = [cached[k] for k in keys]
        return vectors, len(texts) - len(miss_idx)
