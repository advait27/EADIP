# Penetration-Test Scope (Phase 12, SEC-10)

External engagement — required before GA sign-off; this document is the scoping
brief handed to the testing firm. In-repo gates (bandit SAST, pip-audit, trivy
container scan, the AI-safety suite) run in CI and are the baseline the pen-test
builds on, not a substitute for it.

## Targets

- **API gateway** (staging, OIDC mode): all `/v1/*` surfaces.
- **AuthN/Z:** OIDC token validation (alg confusion, audience/issuer bypass,
  expiry), RBAC deny-by-default (privilege escalation between analyst /
  approver / viewer / compliance / admin / custom roles), the dev-auth seam
  MUST be unreachable in staging/prod builds.
- **Tenant isolation:** cross-tenant access attempts on every store (Postgres
  RLS FORCE, per-tenant Qdrant collections, graph ACLs, memory tiers,
  checkpoints, budgets, notifications).
- **NL→SQL safety gate:** SQL injection through the natural-language surface —
  attempt to smuggle writes, UNION exfiltration, cross-tenant predicates,
  function abuse past the sqlglot AST gate.
- **Governed tools (MCP):** argument-gate bypass (extra fields, type confusion),
  credential exposure (`secret_ref` never dereferenced client-side), untrusted
  tool output attempting instruction injection (SEC-06 envelope).
- **Approval gate (G5):** any path to execute a write/high-impact action
  without an APPROVED decision (double-decide, replay, edited-payload races).
- **Admin portal:** guardrail bypass (routing pins, prompt eval gate,
  built-in-role mutation), audit-trail evasion.
- **Platform services:** rate-limit/backpressure bypass, residency guard (451)
  bypass, budget-refusal bypass.
- **Infrastructure:** container escape from the gateway image, k8s RBAC of the
  deploy manifests, secret handling (vault refs only).

## Out of scope

- Denial-of-service beyond validating that the rate limiter and concurrency
  gate respond as designed (no volumetric attacks against shared infra).
- Third-party IdP and LLM-provider internals.
- Social engineering.

## Deliverables & exit

Findings report with CVSS scoring; criticals/highs fixed and re-tested before
GA; the report is compliance evidence (SOC2 CC4.1, ISO 27001 A.8.29).
