# 3. Phase 2 — identity, access & audit (governance core)

Date: 2026-06-26

## Status
Accepted

## Context
Phase 2 makes the platform *governed before capable*: deny-by-default identity,
tenant isolation, and an immutable audit trail (PRD M0; FR-050/051; SEC-01..10;
AP-5; TAD Ch 11). Exit bar: cross-tenant access structurally impossible
(RLS-proven), every authz decision + mutation audited, secrets only in vault.

## Decisions
1. **AuthN: OIDC implemented, SAML seam.** `OIDCVerifier` verifies a Bearer JWT
   (PyJWT + JWKS) with an injectable key resolver so it is unit-testable offline.
   A `dev` auth mode (header-based) remains for local use; it is the single seam
   that must never run in production. SAML has a typed interface, full
   implementation deferred.
2. **AuthZ: PDP, deny-by-default (AP-5/SEC-03).** Pure-logic
   `PolicyDecisionPoint` over an RBAC catalog (role → permission →
   resource/domain/effect). Enforced *and audited* at the route boundary via the
   `require(...)` dependency — authorize at the boundary, not just the gateway.
3. **Tenant isolation: Postgres RLS with FORCE.** `tenant_id` on every row,
   `set_config('app.tenant_id', ...)` per transaction. `FORCE ROW LEVEL SECURITY`
   so even the table owner is subject to the policy; unset tenant denies
   (deny-by-default). Proven by an integration test run in CI against real
   Postgres.
4. **Audit: append-only by trigger (SEC-08).** `audit_event` is insert-only,
   enforced by a `BEFORE UPDATE/DELETE` trigger (defense-in-depth: also REVOKE
   from the app role in prod). Detail is redacted before persistence.
5. **Secrets: vault port (SEC-05).** `SecretsProvider` with env/static adapters;
   a real vault adapter implements the same port. Service JWTs are signed with a
   vault-supplied key; redaction scrubs sensitive fields from logs/audit.
6. **`database_enabled` flag, default off.** Dev/tests use in-memory adapters
   (no DB dependency); production sets it true and runs `eadip-migrate`. RLS and
   append-only are implemented + integration-tested behind this flag.

## Consequences
- Unit tests prove RBAC/OIDC/tokens/redaction/audit and gateway enforcement;
  CI's Postgres service proves RLS isolation + append-only for real.
- **Known follow-up:** JIT provisioning of `app_user`/`tenant` rows from verified
  OIDC claims on first login. Until then, DB-backed run creation requires
  pre-seeded users; the in-memory dev path is unaffected. Tracked for Phase 2.x.
