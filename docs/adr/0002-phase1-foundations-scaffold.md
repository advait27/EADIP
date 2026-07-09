# 2. Phase 1 foundations scaffold

Date: 2026-06-25

## Status
Accepted

## Context
Phase 1 (Foundations & Developer Experience) stands up the skeleton the rest of
the platform plugs into (see EADIP_PHASE1_SPRINTS.md). We need to lock a small
set of foundational choices so later phases compose cleanly.

## Decisions
1. **src layout + uv + hatchling.** Package under `src/eadip/`; deps and lockfile
   via `uv`; build via hatchling. Reproducible installs, clean import isolation.
2. **FastAPI gateway, app-factory pattern.** `create_app()` plus a module-level
   `app` so `uvicorn eadip.gateway:app` matches the TAD container entrypoint.
3. **Ports-and-adapters from day one (AP-10).** `ports/` holds interfaces
   (`ModelClient`, `RunRepository`, `VectorStore`); `adapters/` holds impls.
4. **In-memory RunRepository for Phase 1.** Avoids a hard DB dependency before
   Phase 2/3 wire PostgreSQL + RLS. Swappable via the repository port.
5. **Auth is a dev stub.** `gateway/auth.py` trusts headers / a fixed dev
   identity. Phase 2 replaces it wholesale with OIDC/SAML + RBAC (SEC-02/03).
6. **OpenTelemetry is optional + no-op by default.** Keeps local dev and unit
   tests free of a collector; enabled via the `otel` extra + `EADIP_OTEL_ENABLED`.
7. **LiteLLM seam now, routing policy later.** Minimal provider-agnostic client
   with token/cost accounting (FR-053 seed); full policy in Phase 11.
8. **Eval gate is non-blocking in Phase 1.** Harness + gold-set v1 wired into CI;
   becomes a hard gate in Phase 12 (TAD Ch 12, PRD §13-14).
9. **mypy pragmatic-strict.** `disallow_untyped_defs` + friends now; ratchet to
   full `--strict` in P1-10 hardening.

## Consequences
- Sprint 2's CI/CD, Helm deploy, and eval gate build directly on this layout.
- Phase 2 plugs RBAC/audit into the existing gateway + repository seams without
  rework; the auth stub is the single file to replace.
