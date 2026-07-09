# Compliance Controls Matrix (Phase 12 — PRD §20–22)

How EADIP's implemented controls map onto the frameworks the verticals require.
**Status:** `code` = implemented + tested in this repo (reference cited);
`ops` = operational procedure (runbook in this repo, executed in production);
`external` = third-party evidence gathered before per-vertical sign-off.

| Control (implementation) | SOC 2 | GDPR | ISO 27001 Annex A | EU AI Act | Status |
|---|---|---|---|---|---|
| OIDC authentication; short-lived service JWTs (`gateway/auth.py`, `security/authn/`) | CC6.1 | Art. 32 | A.5.16, A.8.5 | — | code |
| RBAC deny-by-default PDP; custom roles add-only; built-ins immutable (`security/rbac.py`, `/v1/admin/roles`) | CC6.3 | Art. 25, 32 | A.5.15, A.8.2 | Art. 14 | code |
| Tenant isolation: Postgres RLS FORCE on every tenant table; per-tenant vector collections; ACL-scoped graph & memory (`adapters/sql/*`, integration tests) | CC6.1 | Art. 32 | A.8.3 | — | code |
| Append-only audit of every decision, mutation and PII redaction (`security/audit.py`, migration 0002; sweeper never touches it) | CC7.2, CC4.1 | Art. 30 | A.8.15 | Art. 12 (logging) | code |
| PII redaction at ingestion, audited with types found (`ingestion/pii.py` + `pii.redacted` audit event) | CC6.7 | Art. 5(1)(c), 25 | A.8.11 | Art. 10 | code |
| No PII in shared semantic memory; blocked pre-write (safety case `memory_governance`) | CC6.7 | Art. 5, 25 | A.8.11 | Art. 10 | code |
| Erasure cascade per tenant, one audited call (`DELETE /v1/admin/memory/{tenant}`; run/document rows via retention or targeted delete) | — | Art. 17 | A.8.10 | — | code |
| Data retention windows per artifact class, scheduled sweep (`eadip-retention`, retention CronJob) | CC6.5 | Art. 5(1)(e) | A.8.10 | — | code |
| Data residency: per-tenant region pin, 451 on cross-region serving (`platform/residency.py`, data-plane guard) | — | Ch. V (transfers) | A.5.14 | — | code |
| Human approval gate on every write/high-impact action; approver-authority execution (Phase 9, safety suite: 0 unapproved) | CC5.1 | — | A.5.3 (segregation) | Art. 14 (human oversight) | code |
| Verification of every surfaced claim; ungrounded claims suppressed; conflicts flagged never reconciled (Phase 7; eval gate: coverage/grounding ≥95%) | — | — | — | Art. 13, 15 (accuracy/transparency) | code |
| Prompt governance: immutable versions, eval-gated promotion, rollback, full audit (Phase 11) | CC8.1 (change mgmt) | — | A.8.32 | Art. 15 | code |
| Model routing pins + per-tenant budgets + per-run cost bounds (Phases 6/11) | CC5.2 | — | — | Art. 15 | code |
| Rate limiting + backpressure + circuit breakers + graceful degradation (fault-injection-proven, `eadip-reliability`) | A1.1 | — | A.8.6 | — | code |
| Secrets via vault references only; no credentials in code/config (`security/vault.py`, SEC-05) | CC6.1 | Art. 32 | A.8.24 | — | code |
| SAST + dependency + container scanning as blocking CI gates (bandit, pip-audit, trivy) | CC7.1, CC8.1 | Art. 32 | A.8.28, A.8.29 | — | code |
| DR: WAL archiving (RPO ≤5 min), IaC restore (RTO ≤1 hr), quarterly game-day (`docs/runbooks/disaster-recovery.md`) | A1.2, A1.3 | Art. 32(1)(c) | A.8.13, A.8.14 | — | ops |
| SLOs sustained 30 days before GA (`docs/runbooks/slos.md`) | A1.1 | — | A.8.6 | — | ops |
| Penetration test by an external firm (`docs/security/pen-test-scope.md`) | CC4.1 | Art. 32 | A.8.29 | — | external |
| Per-vertical sign-off (HIPAA BAA, SOX ITGC mapping, sector guidance) | — | — | — | — | external |

## EU AI Act posture

EADIP is a **decision-support** system: every autonomous action above read tier
requires human approval (Art. 14), every surfaced claim carries provenance and a
verification status (Art. 13/15), all runs are logged end-to-end with replayable
checkpoints (Art. 12), and PII is minimised at ingestion (Art. 10). Risk
classification per deployment vertical is completed at sign-off.

## Evidence index

- Gates: `make lint type test eval safety reliability load security` — all
  blocking in CI (`.github/workflows/ci.yml`).
- ADRs 0001–0013 document every architectural control decision.
- The audit trail itself (append-only, RLS-scoped) is the runtime evidence.
