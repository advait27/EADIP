# EADIP — Phase 1 Sprint Breakdown (Foundations & Developer Experience)

**Phase goal:** Stand up the skeleton everything else plugs into — a request flows through an empty pipeline, traced, and ships to staging via gated CI/CD.
**Phase exit (DoD):** A no-op run is accepted end-to-end, traced with a correlation id, and auto-deployed to staging; eval-gate + model-routing stubs live.
**Maps to:** PRD M0 · FR-001, FR-053(seed), FR-054(seed), FR-055 · NFR-04/11/12 · TAD Ch 3, 15, 18 · AP-6/7/9/10.
**Duration:** 2 sprints (2 weeks each). **Squad assumption:** 1 cross-functional squad. **Capacity:** ~28–30 pts/sprint (calibrate after Sprint 1).

> Story IDs map to the backlog CSV epic **P1-Foundations**. Estimates in story points (Fibonacci). Owners are roles, not names.

---

## Sprint 1 — "Skeleton breathes locally"
**Sprint goal:** A request is accepted by the FastAPI gateway, traced end-to-end, and the whole stack runs locally with one command.

| ID | Ticket | Type | Pts | Owner | Acceptance criteria |
|----|--------|------|-----|-------|---------------------|
| P1-1 | Scaffold monorepo per TAD §18.3 | Story | 5 | Tech Lead | Repo builds; `uv sync --frozen` works; package import graph respects ports-and-adapters; pre-commit (ruff+black) green |
| P1-2 | API Gateway: `POST /v1/runs` + SSE skeleton | Story | 8 | Backend | `POST /v1/runs` → 202 + Run object; `GET /v1/runs/{id}/events` streams a heartbeat SSE; OpenAPI auto-generated |
| P1-3 | Containerize services (multi-stage, non-root) | Story | 5 | Platform | Image builds under target size; runs as uid 10001; `CMD` serves gateway on :8000; digests pinned |
| P1-4 | Local dev via Docker Compose | Story | 3 | Platform | `docker compose up` brings up gateway + Postgres + Redis + Qdrant + Neo4j; healthchecks pass |
| P1-6 | OpenTelemetry + structured logging bootstrap | Story | 5 | Platform/SRE | Every request emits a trace with a correlation id, visible in the trace backend; JSON logs |
| XC | Cross-cutting reserve (security/eval/debt) | — | ~3 | Squad | Threat-model the gateway surface; debt log opened |

**Sprint 1 committed points:** ~26 + reserve.

**Sub-tasks (larger tickets):**
- **P1-2** → (a) FastAPI app + routing + Pydantic `CreateRun`/`Run` models; (b) SSE channel + heartbeat publisher; (c) request-id middleware; (d) OpenAPI export check in CI.
- **P1-1** → (a) package tree; (b) `pyproject.toml` + `uv.lock`; (c) `ports/` + `adapters/` interface stubs; (d) pre-commit + ruff/black config.
- **P1-6** → (a) OTel SDK wiring in gateway; (b) collector in Compose; (c) correlation-id propagation; (d) redaction filter on log payloads.

**Demo:** `curl POST /v1/runs` → 202; open SSE → heartbeat; show the trace + correlation id in the backend; whole stack from `docker compose up`.

---

## Sprint 2 — "Gated pipeline ships it"
**Sprint goal:** Gated CI/CD builds, scans, signs, and deploys the traced skeleton to staging; eval-gate and model-routing stubs are live. **= Phase 1 exit.**

| ID | Ticket | Type | Pts | Owner | Acceptance criteria |
|----|--------|------|-----|-------|---------------------|
| P1-5 | CI/CD pipeline with gated stages | Story | 8 | Platform/SRE | Failing lint/type/test blocks merge; signed image pushed; auto-deploy to staging succeeds; stages: lint→type→test→eval-gate(placeholder)→SAST/scan→build/sign→stage |
| P1-7 | LiteLLM abstraction (minimal) + provider config | Story | 5 | AI/Backend | A test prompt routes through LiteLLM to a configured provider; tokens/cost recorded on the OTel span; single-tier routing stub |
| P1-8 | Eval harness skeleton + gold-set v1 seed | Story | 5 | AI/Eval | Harness runs on gold-set v1 (grader interface defined); CI eval-gate stage executes as a non-blocking placeholder |
| P1-9 | Base Kubernetes/Helm manifests | Story | 5 | Platform/SRE | `helm install` deploys gateway to a namespace; readiness/liveness probes pass; staging values committed |
| P1-10 | Phase-1 hardening + exit checklist | Task | 3 | Squad | Phase exit DoD verified end-to-end (see below); demo recorded |
| XC | Cross-cutting reserve | — | ~3 | Squad | Eval graders for FR-001 stub; secret-scan in CI confirmed |

**Sprint 2 committed points:** ~26 + reserve.

**Sub-tasks (larger tickets):**
- **P1-5** → (a) PR workflow (lint+type+test); (b) container/SAST scan stage; (c) image sign + push to registry; (d) ArgoCD/Helm staging deploy; (e) branch protection wiring.
- **P1-7** → (a) LiteLLM client behind `ports/model_client`; (b) provider config via pydantic-settings (vault-ready); (c) token/cost capture into span attributes.
- **P1-8** → (a) grader interface + 1 reference grader; (b) gold-set v1 (5–10 seed cases); (c) `make eval` + CI stage hook.

**Demo:** open a PR → watch the pipeline gate, scan, sign, deploy to staging; hit the staging URL; show LiteLLM call cost on a trace; run `make eval` against gold-set v1.

---

## Phase 1 Exit Checklist (verify before declaring Phase 1 done)
- [ ] No-op run accepted via `POST /v1/runs` and observable via SSE.
- [ ] Full request trace with correlation id in the trace backend (AP-9).
- [ ] One-command local stack (`docker compose up`) — gateway + 4 stores healthy.
- [ ] Gated CI/CD: lint/type/test/scan/sign/deploy; failing gate blocks merge.
- [ ] Signed image auto-deployed to staging via Helm; probes green.
- [ ] LiteLLM routing stub records tokens/cost on spans (FR-053 seed).
- [ ] Eval harness runs on gold-set v1; CI eval-gate stage present (FR-054 seed).
- [ ] Repo respects ports-and-adapters; mypy/pyright strict on core; pre-commit green (AP-10, NFR-11).
- [ ] No secrets in code/logs/config (SEC-05 baseline).

## Carry-forward into Phase 2
Phase 2 (Identity, Access & Audit) starts immediately on the FastAPI gateway + Postgres skeleton from here: SSO/OIDC, RBAC, Postgres RLS, vault, and the append-only audit subsystem. The auth middleware hook should be stubbed in P1-2 so Phase 2 plugs in without rework.

---
*Living artifact. Re-baseline points after Sprint 1 actuals. Pairs with EADIP_SCRUM_PLAN.md, the PRD, and the TAD.*
