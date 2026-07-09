# 8. Phase 7 — verification, recommendation & reporting (Alpha)

Date: 2026-07-03

## Status
Accepted

## Context
Phase 7 makes answers trustworthy and reaches the **Alpha** gate: independently
re-derive every number, attach calibrated confidence + provenance, recommend
actions, and produce the executive brief (PRD M4; FR-041..045; EXP-01..06; TAD
Ch 8.3; AP-1/3). Exit bar: one investigation type works end-to-end with
independent verification + full audit, and the eval harness is live and graded.

## Decisions
1. **Verification re-derives, it does not trust.** For every analytics finding
   the verifier re-validates the *exact source SQL* through the Phase 5 safety
   gate (a fresh safety check) and re-executes it on the warehouse, then
   **recomputes** the reported magnitude via an independent path — the Phase 5
   statistics functions applied to the fresh rows (driver Δ, headline total,
   anomaly value + still-flagged check, forecast next-value, correlation r).
   Match → verified; disagree → **conflicting** (surfaced, never silently
   reconciled); can't re-run → unverified. Column roles are inferred from the
   result, so it needs no coupling to the generated column names.
2. **Findings carry `kind` + structured `detail`** (added to the orchestrator
   `Finding`) so re-derivation is self-contained; the headline finding is bound
   to the *driver* query so its total is recomputed from real rows.
3. **Calibrated confidence, not a logprob** (FR-042): a transparent rubric of
   verification status + independent-source corroboration (retrieval passages
   sharing content tokens) − a penalty for association-only claims. Assumptions
   and limitations are stated explicitly in the brief.
4. **Recommendations consume only verified, non-association claims** (FR-043),
   ranked by impact × confidence. Correlation is never turned into a causal
   action — association-only findings go to the brief's limitations instead.
5. **Layered executive brief** (EXP-01..06): headline → key findings (status +
   confidence + provenance) → recommendations → assumptions/limitations → raw
   drill-down (all claims + the SQL queries retained for audit). Conflicting and
   unverified claims are always shown.
6. **Verify+report is the engine's terminal stage.** The orchestrator gains an
   optional reporter; after reflection it runs verification, stores the verified
   claims + brief on `RunState`, and streams `claim.verified` /
   `verification.summary` / `recommendation` / `brief`. `GET /v1/runs/{id}/report`
   (RBAC, audited) returns the brief + drill-down.
7. **Memory is the checkpoint (FR-045, minimal).** The full `RunState` —
   plan, findings, verified claims, brief — is already persisted per node by the
   Phase 6 checkpointer, so it is the run's durable short/long-term memory; no
   separate store was added.
8. **The eval harness is now live and graded.** `eadip-eval` runs the real
   orchestrated + verified pipeline over an alpha gold set and grades the produced
   brief (no more echo placeholder). Requires the `data` extra (DuckDB).

## Consequences
- The Alpha investigation runs end-to-end: a complex question → streamed plan →
  parallel retrieve+analyze → reflect → **independent verification** → a verified
  one-page brief with per-claim provenance + confidence + recommendations, all
  audited. Demonstrated live, including the verifier flagging a fabricated
  magnitude as conflicting (claimed −999 vs re-derived −220).
- Verification is only as independent as the source it re-runs against: it
  re-executes the *same* warehouse, so it catches stale/incorrect aggregation and
  unsafe SQL, but not a systematically wrong warehouse. Cross-source
  corroboration (retrieval) is a secondary, token-overlap signal, not a semantic
  entailment check. Confidence is a calibrated rubric, not a probability.
- Recommendation phrasing is deterministic/heuristic (LLM seam present); it cites
  verified claims rather than inventing actions.
- Phase 8 (MCP) adds governed external tools as new executors; verification then
  extends to cross-source corroboration across those tools.
