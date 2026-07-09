# 12. Phase 11 — Platform Services & Administration

Date: 2026-07-09

## Status
Accepted

## Context
Phase 11 operationalizes governance, memory and administration so the platform
is configurable, learns with use, and stays cost-bounded (PRD M3/M7; FR-045 full,
FR-052/053/056/057; TAD Ch 10/14; NFR-10/13; US-D2/D3). Exit bar: admins
self-serve RBAC/prompts/routing/budgets; memory improves repeat questions
without leaking; per-run cost capped & tracked.

## Decisions
1. **Three-tier memory behind ports, consolidation by a Memory Agent (FR-045).**
   Working memory is the Phase 6 checkpoint (already durable). New `memory/`
   adds **episodic** (per-tenant: question → the plan that answered it → outcome)
   and **semantic** (durable, cross-run facts) tiers behind `EpisodicStore`/
   `SemanticStore` ports — in-memory reference default, Postgres (RLS FORCE,
   migration 0007) behind the same ports. The `MemoryAgent` consolidates a
   cleanly-finished run: the plan becomes a replayable episode keyed on
   normalised content tokens (two phrasings of the same ask collapse), and
   verified, non-association, high-confidence claims become subject/predicate/
   object facts. **Governance:** any candidate fact containing PII is dropped
   before write (never stored-then-redacted), and `forget(tenant)` is a
   one-call erasure cascade across both tiers (admin portal `DELETE
   /v1/admin/memory/{tenant}`, high-impact grant, audited).
2. **Plan reuse skips re-planning, never governance.** The engine consults
   episodic memory only when it has no plan yet; a hit streams `plan.reused`
   and the replayed plan still goes through routing, execution, the Phase 9
   approval gate and Phase 7 verification. Reuse is tenant-scoped; a hit
   increments the episode's usefulness counter.
3. **Prompts are governed artifacts: immutable versions + eval-gated serving
   (FR-052, US-D2).** `platform/prompts.py`: registering always creates version
   N+1 (content sealed by sha256; the Postgres table enforces immutability with
   a trigger). A version cannot serve traffic — canary or active — without a
   recorded eval score ≥ threshold (default 0.8): `promote`/`start_canary`
   refuse otherwise. Canary serves a deterministic hash-bucketed fraction;
   **one-click rollback** reinstates the most recently active RETIRED version
   and marks the demoted one ROLLED_BACK (never auto-re-picked). The shipped
   agent instructions are seeded as version-1 ACTIVE at startup; the LLM agents
   fetch the active instruction **per call** through a provider seam, so a
   promote/rollback applies to running processes immediately (heuristic agents,
   the default, use no prompt — disclosed).
4. **Model routing: per-task tiers, hard pins, budget-aware degradation
   (FR-053, US-D3).** `platform/routing.py` maps each task (interpret/plan/
   reflect/verify/recommend/sql/rerank/…) to a strong/standard/light tier with
   two rules: **planning and verification are pinned STRONG** (an admin override
   below strong is refused — 422 guardrail), and unpinned tasks degrade one tier
   at a time when the remaining budget can't afford the tier's estimated
   per-call cost. `build_agents` routes the LLM agents' models through the
   policy; per-call dynamic degradation inside a run is a Phase 12 hook (the
   heuristic default calls no model at all).
5. **Per-tenant budgets refuse new work, never truncate in-flight work.**
   `BudgetLedger` (in-memory default, `tenant_budget` table behind the port)
   holds a monthly cap + running spend. The gateway refuses **new** runs with
   429 once the cap is exhausted; in-flight runs stay bounded by the Phase 6
   per-run cost ceiling, so worst-case overspend is one run's cap. The SSE
   route charges the run's actual final cost on `run.done`. Executors now
   attribute a **nominal deterministic compute cost per step** so cost is
   tracked (and budgets are meaningful) even on the fully-offline path.
6. **Notifications tap the stream the gateway already relays (FR-056).** The
   SSE relay maps `approval.required` → action-required, `run.done` →
   completion, anomaly findings → warning, budget exhaustion → warning, and
   fans out via a channel port: in-memory inbox (dev default + portal listing),
   structured-log channel (ops pipelines), webhook channel (lazy httpx) for
   Slack/Teams/e-mail bridges. No coupling added inside the engine.
7. **Rate limiting + backpressure at the edge (NFR-10/13).** A per-tenant token
   bucket on `/v1/*` (429 + Retry-After; health exempt) and a per-tenant
   concurrency gate on orchestration streams (429 instead of queueing —
   shed early, AP-7). Both clock-injected and per-replica by design; the
   cross-replica limit belongs to the ingress. Queue partitioning ships as a
   KEDA `ScaledObject` (CPU + per-shard Redis-list depth) in `deploy/k8s/`.
8. **The Administration Portal is a governed API, not a bypass.** `/v1/admin/*`
   requires the `admin/platform` grant (deny-by-default — only the admin role's
   wildcard matches), every mutation is audited, and erasure requires the
   high-impact effect. Surfaces: role catalog + custom roles (built-ins
   immutable; a role only ever adds explicit grants), prompt lifecycle, routing
   table + overrides, budgets, runtime feature flags (seeded from settings,
   flipped without redeploy, unknown flags read false), memory status/erasure,
   notification inbox. User/role *assignment* stays in the enterprise IdP
   (SEC-02); connectors already have `/v1/tools` (Phase 8).

## Consequences
- Repeat questions skip re-planning (memory) while remaining fully governed;
  memory never holds PII and is erasable per tenant in one audited call.
- Prompt changes are as controlled as code changes: versioned, eval-gated,
  canaried, and reversible in one click — and take effect without redeploys.
- Runs are cost-attributed end-to-end: per-step nominal costs → per-run ceiling
  (Phase 6) → per-tenant monthly cap (Phase 11), with notification on exhaustion.
- The safety suite grows to six checks (`memory_governance`: PII-blocked +
  erasure cascade) and stays a hard CI gate.
- **Disclosed scope:** no web frontend ships in this repo, so WCAG 2.1 AA
  applies to the future UI consuming these APIs (the layered brief remains the
  UI contract); budget rollover is a monthly job (`reset_spend` seam, not yet
  scheduled); notifications inbox is per-replica in dev (webhook/log channels
  are the durable paths); per-call budget-aware model degradation inside a run
  is deferred to Phase 12.

Gate: ruff + format + mypy (154 files) + pytest (265 passed, 15 skipped) +
eadip-eval 6/6 (live, graded) + eadip-safety 6/6 — GREEN.
