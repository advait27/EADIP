# 6. Phase 5 — analytics engine (NL→SQL + BI)

Date: 2026-06-26

## Status
Accepted

## Context
Phase 5 turns governed warehouses into explanations: safe NL→SQL plus the
statistics that say *why* a number moved (PRD M2; FR-020..024; TAD Ch 8.1/8.2;
NFR-06). Exit bar: a verified metric-movement explanation is produced; **no
non-read-only SQL can execute**; every claim carries its query.

## Decisions
1. **The AST safety gate is the single chokepoint.** Every candidate query —
   template- or LLM-generated — is parsed with `sqlglot` and rejected unless it
   is one read-only `SELECT` with: only schema tables/columns (no `*`, no
   catalog access), only allow-listed functions (this is what blocks engine
   file/system functions like `read_csv`/`pg_read_file`), a required
   `tenant_id = '<this tenant>'` predicate with no foreign/loose tenant
   comparison, no `UNION`/`OR` (so tenant scoping stays sound), and a cost guard
   (keyed joins only, bounded table count). The gate returns *re-rendered* SQL,
   so comment tricks die too. `sqlglot` is a **core** dependency precisely so the
   gate is always importable and always unit-tested.
2. **Defence in depth at the engine.** DuckDB runs queries on a `read_only`
   connection; the Postgres replica runs them in a `transaction_read_only`,
   RLS-scoped transaction with a `statement_timeout`. RLS is the *hard*
   tenant-isolation guarantee — independent of the SQL text — behind the gate's
   cooperative tenant-predicate check. The row cap is enforced by wrapping.
3. **Deterministic defaults, real seams** (mirrors Phase 4). NL→SQL defaults to a
   schema-aware template generator (offline, always-valid), with an
   `LLMSqlGenerator` behind the same port. A bounded repair loop re-generates on
   validation failure / empty results, feeding the validator's reason back to
   the model. A result cache keys on (tenant, exact validated SQL).
4. **BI statistics are pure-stdlib and exact.** Additive driver attribution,
   Pearson correlation with a proper Student-t p-value (regularised incomplete
   beta) and Benjamini-Hochberg FDR control, robust (median/MAD) anomaly
   detection, and an OLS forecast with a real prediction interval — no numerical
   libraries, fully unit-tested against known values.
5. **Correlation ≠ causation, enforced in output** (FR-023): correlation findings
   are labelled `association_only` and say so in their text.
6. **Provenance seed** (FR-024): every finding is bound to the exact SQL that
   produced it, and the route audits each executed statement (`sql.execute`)
   with its SQL. Phase 7's verifier re-executes these from a fresh context.
7. **DuckDB is the in-process default; Postgres is the replica path.** The
   `Warehouse` port exposes a typed schema (the validator's allowlist) + a
   read-only `execute`. DuckDB seeds the deterministic demo finance dataset per
   tenant so the demo runs offline; `0004_analytics.sql` provisions the
   RLS-scoped `finance_metrics` fact table for the Postgres path.

## Consequences
- The "why did EMEA margin fall?" demo produces a ranked, magnitude-bearing
  driver list (Hardware −220, 96% of the −230 decline) with an anomaly, a
  forecast, association-only correlations, and a query behind every claim — all
  via the governed `POST /v1/analytics` (RBAC `metrics/read`, audited).
- The safety gate is proven by an adversarial unit battery (DML/DDL, multi-
  statement, `UNION`/`OR`, `*`, unknown table/column, file/system functions,
  missing/foreign/loose tenant predicate, cartesian/over-budget joins). The
  engine-level read-only + RLS guarantees are proven against DuckDB (local/CI)
  and Postgres (CI).
- Analytics requires the `data` extra (DuckDB); the safety gate and BI stats need
  no engine. The template generator is intentionally narrow — the LLM generator
  + repair loop is the path to broad question coverage. DuckDB has no statement
  timeout, so that bound is a Postgres-replica control (documented).
- Phase 6's orchestrator calls `AnalyticsService.analyze` as an executor; Phase 7
  verifies findings by re-running their `evidence_sql`.
