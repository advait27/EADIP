# 11. Phase 10 — GraphRAG & knowledge graph (Private Beta)

Date: 2026-07-07

## Status
Accepted

## Context
Phase 10 answers structural "why" questions from **explicit metric lineage** the
graph encodes, rather than inference over prose (PRD M6; FR-013; TAD Ch 4.4/7.2;
G2 accuracy) — and is the **Private Beta** gate. Exit bar: driver analysis via
lineage traversal works; ≥90% accuracy on a partner gold set; RBAC + audit +
approval workflow in production use with design partners.

## Decisions
1. **A typed lineage meta-model behind a port.** `ports/graph.py` defines the
   node kinds (Metric / Dimension / DataSource / Entity / Event / Doc / Team) and
   edge kinds (`DERIVED_FROM` with signed weight/direction, `SLICED_BY`,
   `IMPACTS`, `SOURCED_FROM`, `OWNED_BY`, …) plus a tenant-scoped `KnowledgeGraph`
   Protocol (`upsert_lineage`, `neighborhood`). The graph makes explicit what the
   warehouse only implies: `gross_margin = revenue − cogs`, sliced by
   region/product_line, with a COGS-spike **Event** that `IMPACTS` cogs (−220).
2. **Ports-and-adapters, in-memory reference default (as every prior phase).** The
   `InMemoryKnowledgeGraph` does a bounded, breadth-first neighbourhood walk (both
   edge directions — lineage is traversed upstream) and is the offline-
   deterministic reference the tests + demo run against. `Neo4jKnowledgeGraph`
   implements the same port with a `(tenant_id, key)` **uniqueness constraint**
   per node kind and a bounded variable-length Cypher path; `neo4j` is lazily
   imported (the `graph` extra). No heavyweight dependency on the default path.
3. **Bounded traversal → relevant subgraph → linearize-to-evidence.** GraphRAG
   detects the seed metric from the question (deterministic alias match; an LLM
   detector fits the same `seed_for` seam), extracts the `hops`-bounded
   neighbourhood, and **linearizes** it to short, provenance-bearing sentences
   (`source_type="graph"`, `source_ref="graph:<metric>"`). Impact edges lead (they
   answer "why"), then derivation, then slicing — ordered and deterministic.
4. **Merged with vector hits, not bolted on.** The Retriever gains an optional
   GraphRAG service; on **deep** effort it fetches lineage evidence and merges it
   with the fused vector passages, de-duplicated by id and **prepended after
   rerank** so decisive lineage isn't ranked out. Simple/standard effort is
   unchanged. Wired through the planner's existing effort levels — a "why"
   question interprets as `deep`, so lineage traversal fires automatically.
5. **Graph evidence is identity-scoped like everything else (AP-5).** Nodes carry
   `acl_tags`; both the in-memory walk and the Cypher query filter to nodes the
   caller may see, so a user never traverses lineage outside their access.
6. **Lineage flows into the verified brief.** Graph passages become retrieval
   findings and pass through Phase 7 verification (verified by their source
   pointer, like any grounding extract) into the brief's drill-down — so the
   COGS-spike event that drove the fall appears alongside the re-derived numbers.
7. **Dev seeds the demo lineage lazily.** `SeedingGraphRAGService` ingests the
   demo finance lineage for a tenant on first use (only when the graph is empty),
   so a deep investigation has lineage to traverse with no external store. In
   production, lineage is ingested ahead of time via the same `upsert_lineage`
   path a real entity/relation-extraction pipeline would use.

## Consequences
- A deep "why did gross margin fall?" run traverses the lineage and surfaces
  "Event 'EMEA Hardware COGS spike (2026-Q2)' impacted Cost of Goods Sold,
  lowering Gross Margin by 220" — the structural driver the vector store can't
  express — merged with vector + analytics evidence in one verified brief. The
  eval gold set gains a lineage case; the live+graded suite is 6/6.
- The seed detector and demo lineage are deterministic/heuristic (LLM extraction
  and a real Neo4j ingestion pipeline are the production build-out); the graph is
  only as complete as what is ingested, so absent lineage simply yields no graph
  evidence (never a wrong edge).
- **Private Beta gate:** the accuracy bar and "in production use with design
  partners" are partner-facing milestones, tracked outside this repo; the
  engineering that makes them attainable — lineage traversal answering driver
  analysis, RBAC + audit + the Phase 9 approval workflow — is complete and
  demonstrated. Neo4j round-trip/traversal/isolation are proven CI-only (no local
  Docker), consistent with the Postgres/Qdrant integration tests.
- Phase 11 (Admin/Platform) adds lineage ingestion management + memory; the graph
  port is the seam a GraphRAG-aware Planner effort tier and richer traversals
  (multi-seed, path-ranked) extend behind.
