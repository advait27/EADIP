# 13. Phase 12 — Hardening, Scale, Compliance & GA

Date: 2026-07-09

## Status
Accepted

## Context
Phase 12 proves the platform at scale, under faults, and against compliance
frameworks, and turns every quality bar into a **hard, blocking gate** (PRD M7;
NFR-02/03/04/06; §16/§20–23; TAD Ch 16). The GA gate has two evidence classes:
engineering evidence (produced and enforced in this repo) and operational
evidence (produced by running the system — 30-day SLOs, external pen-test,
per-vertical sign-off).

## Decisions
1. **Reliability is proven by fault injection, not asserted.** `eadip-reliability`
   (`eval/reliability.py`, 6 scenarios against the real pipeline): crash mid-run →
   a NEW engine resumes from the checkpoint with **exactly-once** step execution;
   replaying a DONE run executes zero steps (idempotent redelivery); transient
   faults recover via retry; a flapping tool trips the circuit breaker, refuses
   fast, and recovers through the half-open probe; a hard-down retrieval backend
   still yields a partial verified brief; runaway cost stops bounded and clean.
   Blocking in CI. Writing the suite immediately caught a real Phase 8 gap —
   `McpManager.invoke` only caught `TransportError`, so an unexpected transport
   exception escaped the governed path; it now returns a breaker-counted refusal
   for ANY fault class.
2. **Load is a measured gate with honest cluster math (NFR-02).** `eadip-load`
   drives the REAL app in-process over ASGI (no mocks) with N concurrent
   virtual users, each a distinct tenant, on a 70/20/10 search/analytics/full-run
   mix, and exits non-zero on error-rate or read-path p95 breaches. NFR-02 is
   200 concurrent **per cluster**; the deployment floor is 2 replicas (Helm,
   KEDA 2→12), so the single-node gate runs the per-replica share (100 users:
   search p95 ~150 ms, analytics p95 ~1.96 s, 0% errors on reference hardware).
   The gate immediately caught a real hot-path bottleneck: the DuckDB warehouse
   serialized every query behind one global lock (analytics p95 9.3 s at 200
   users). Fixed by seeding (writes) under the lock but running queries on
   **MVCC cursors of one shared connection** — p95 dropped 2.8×, throughput
   39→95 req/s. The read-only-connection defence that design change removed is
   replaced by a structural one: every statement executes as a table subquery,
   so any non-SELECT is an engine-level syntax error (test updated to pin the
   new invariant; the AST gate remains the primary control).
3. **The eval harness is now a hard gate with verification metrics.**
   `eadip-eval` fails CI below: accuracy ≥ 92% (gold-set pass rate), **verified
   coverage ≥ 95%** (claims checked by a real method, not surfaced unchecked),
   **grounding ≥ 95%** (verified claims carrying provenance). Measured: 6/6,
   66 claims, 100% coverage, 100% grounding. Safety (6/6) and reliability (6/6)
   are also blocking CI steps; the CI-sized load smoke (50 users) guards the
   concurrency machinery on shared runners while the 100-user gate is the
   release-cut evidence on perf hardware.
4. **Retention is a planned, parameterised sweep — and the audit trail is
   exempt.** `eadip-retention` (dry-run default, `--apply` to delete) expires
   runs+checkpoints / memory tiers / document lineage on per-class windows
   (`retention_*_days`, 0 = keep forever); pure `plan()` is unit-tested;
   `audit_event` is never swept (append-only compliance evidence, archived by
   ops). Scheduled as a nightly CronJob beside the DR backups.
5. **Data residency is enforced at the application layer, per tenant.** A
   tenant pinned to a region (admin portal, audited; `tenant_residency`,
   migration 0008) is refused with **451** by any deployment in another region —
   on every data-plane route (runs/search/analytics/tools), before data access.
   Storage-layer replication stays per-region (DR runbook); the guard makes
   cross-region serving impossible even when misrouted.
6. **PII redaction is audited.** The ingestion pipeline records a `pii.redacted`
   audit event (types found + source ref, never values) — GDPR minimisation
   with evidence.
7. **Security gates are real and blocking.** bandit SAST (config in pyproject;
   findings fixed, not suppressed: sha1 feature-hashing declared
   `usedforsecurity=False`, hardcoded Neo4j default credentials removed; the two
   gate-validated SQL wrappers annotated as by-design) + pip-audit (clean) in
   the quality job; trivy image scan blocking on HIGH/CRITICAL in the build job.
   Image signing (cosign keyless) activates when the org registry is wired
   (needs a pushed digest). The external pen-test is scoped in
   `docs/security/pen-test-scope.md`.
8. **DR is IaC + runbook with explicit RPO/RTO budgets.** Continuous WAL
   archiving (`archive_timeout=60s` → RPO ≤ ~1 min ≪ 5 min) + 6-hourly base
   backups (CronJob), a supervised PITR restore Job, warm-standby promotion for
   cross-region, and a quarterly game-day checklist with rehearsed timings
   inside the 1 hr RTO (`docs/runbooks/disaster-recovery.md`). Vector/graph
   stores are rebuildable and degrade gracefully (proven by the reliability
   suite), so authoritative-state RPO rests on Postgres alone.
9. **Compliance is a controls matrix with per-control evidence.**
   `docs/compliance/controls-matrix.md` maps every implemented control to
   SOC 2 / GDPR / ISO 27001 Annex A / EU AI Act, tagged `code` (implemented +
   tested here), `ops` (runbook), or `external` (pen-test, sign-offs). The EU
   AI Act posture is decision-support: human approval on all writes (Art. 14),
   provenance + verification on all claims (Art. 13/15), end-to-end logging
   (Art. 12), PII minimisation (Art. 10).

## Consequences
- Every quality bar is a blocking CI gate: lint, format, types, tests, eval
  (accuracy/coverage/grounding), safety, reliability, load smoke, SAST,
  dependency audit, container scan.
- Two real defects were found and fixed by building the gates — the ungoverned
  transport-fault path and the analytics hot-path serialization — which is the
  point of Phase 12.
- **GA gate status: engineering evidence complete and green.** Remaining for
  the GA sign-off, and only obtainable operationally: the 30-day sustained SLO
  window in production (`docs/runbooks/slos.md`), the external penetration
  test, a rehearsed cross-region game-day on real infrastructure, and
  per-vertical compliance sign-off. The repo can not and does not claim these.

Gate: ruff + format + mypy (158 files) + pytest (274 passed, 15 skipped) +
eval 6/6 (66 claims, 100% coverage/grounding) + safety 6/6 + reliability 6/6 +
load (100 users: 0% errors, read-path p95 < 2 s) + bandit 0 + pip-audit 0 — GREEN.

## Amendment (2026-09-27): what the coverage gate measured

The "verified coverage" gate above counted claims whose method was not `none`.
An audit showed this is near-tautological: analytics claims that could not be
recomputed (or carried no magnitude) were still labelled `verified`, and
retrieval/tool claims are verified by a source pointer. The 100% figures above
are therefore not evidence of correctness. From this amendment, those claims
are `unverified`, every claim records `value_checked`, and the gate is
**checked coverage**: the share of analytics claims whose label rested on a
value comparison. Pointer-grounded retrieval/tool claims are reported
separately. The earlier numbers stand as a record of what was measured.
