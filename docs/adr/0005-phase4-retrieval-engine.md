# 5. Phase 4 — retrieval engine (hybrid search)

Date: 2026-06-26

## Status
Accepted

## Context
Phase 4 turns the ingested corpus into grounded, identity-scoped evidence
(PRD M1; FR-010/011/012/014; TAD Ch 7.1; AP-5). Exit bar: grounded retrieval over
a tenant corpus; filtered documents provably never influence ranking.

## Decisions
1. **Filter in the store, pre-ANN (AP-5).** The `VectorStore` port gains
   `search_dense`/`search_sparse` taking a `SearchFilter` (ACL tags, sources,
   as-of). The store applies it before similarity ranking, so documents the user
   may not see never appear *and never influence scores*. `InMemoryVectorStore`
   is the authoritative reference semantics; `QdrantVectorStore` translates the
   same filter to a Qdrant `Filter` (validated in CI).
2. **ACL model.** A chunk with no `acl_tags` is public within the tenant; a
   tagged chunk requires the caller to hold a matching tag (any-match,
   deny-by-default). Time-aware: `valid_from <= as_of < valid_to`, nulls
   open-ended (FR-011).
3. **RRF over dense + sparse + sub-queries.** Manual Reciprocal Rank Fusion is
   rank-based and scale-free, so it composes hybrid search and multi-query
   expansion in one step and stays store-agnostic and unit-testable.
4. **Deterministic defaults, real seams.** Multi-query expansion defaults to a
   heuristic keyword expander (LLM expander behind the same port); reranking
   defaults to a lexical token-overlap reranker (cross-encoder seam, lazy);
   context compression is extractive within a char budget. All deterministic so
   the pipeline is fully testable without a model.
5. **Governed search surface.** `POST /v1/search` reuses Phase 2 governance:
   `require("knowledge","*",read)` enforces RBAC, the decision + the search are
   audited, and the query is tenant-scoped. ACL tags are derived from the
   caller's roles for now (documented bridge); a richer identity→data-domain
   grant mapping replaces it later.
6. **Shared vector store.** The gateway exposes one process-wide vector store so
   ingestion and retrieval operate on the same data.

## Consequences
- The hybrid flow (expand → embed → dense+sparse search → RRF → rerank →
  compress) is proven end-to-end in-memory; the Qdrant filter translation is
  proven in CI. A live ingest→search demo shows ACL filtering working.
- Phase 6's orchestrator consumes `RetrievalService.retrieve`; GraphRAG
  (Phase 10) adds a graph traversal branch that merges into the same evidence.
