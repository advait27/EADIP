# 4. Phase 3 — data stores & document/embedding pipeline

Date: 2026-06-26

## Status
Accepted

## Context
Phase 3 turns raw documents into governed, embedded, ACL-tagged chunks in a
per-tenant vector store (PRD M1; FR-015/016; NFR-08; TAD Ch 4/7). Exit bar: a
tenant corpus ingests cleanly; chunks carry ACL + time payload; PII is redacted
in stored artifacts.

## Decisions
1. **Ports + swappable backends.** `Embedder`, `SparseEncoder`, `EmbeddingCache`,
   `VectorStore`, `DocumentRepository` ports with in-process defaults (hash
   embedder, in-memory cache + store) so the whole pipeline is unit-testable with
   no external services; production flips settings to LiteLLM / Redis / Qdrant.
2. **Pipeline order: parse → redact PII → chunk → embed (cache-first) → sparse →
   upsert.** PII is redacted *before* chunking/embedding so no raw PII reaches the
   vector store (NFR-08). The embedding cache keys on content hash + model
   version, so re-ingesting identical content makes zero model calls.
3. **Per-tenant Qdrant collections (`kb_<tenant_hex>`)** with named `dense`
   (cosine) + `bm25` sparse vectors and payload indexes on `acl_tags` +
   `valid_from`, so identity/time filters apply pre-ANN at retrieval (AP-5,
   TAD Ch 4.5). Sparse vectors are produced now (hashing TF) to set up Phase 4
   hybrid search.
4. **Deterministic hash embedder for dev/tests.** Stable unit-norm vectors with
   no network — reproducible tests and real cache hits. Real embeddings via
   `LiteLLMEmbedder` behind the same port.
5. **`data` extra, lazily imported.** qdrant-client, redis, pypdf, python-docx,
   openpyxl live in an optional extra; adapters/parsers import them lazily, so
   the default install and unit tests stay light. CI installs the extra + runs a
   Qdrant service to execute the integration test.
6. **Relational lineage + pgvector provisioned.** Migration 0003 enables the
   `vector` extension and adds a tenant-scoped, RLS-protected `document` table for
   ingestion provenance. Chunk vectors live in Qdrant (primary); pgvector is
   provisioned for the small-corpus backend option (TAD Ch 4.1).

## Consequences
- The pipeline is provable end-to-end in-memory (unit tests) and against real
  Qdrant (CI); the `eadip-ingest` CLI exercises the real entry point.
- Retrieval (Phase 4) builds on the `VectorStore` port (search), the sparse
  vectors, and the ACL/time payload indexes created here.
