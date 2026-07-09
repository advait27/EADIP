# EADIP — Scrum Delivery Plan (12 Phases)

**Project:** Enterprise Autonomous Decision Intelligence Platform (EADIP)
**Companion docs:** `EADIP_PRD.docx` (what/why) · `EADIP_TAD.html` (how)
**This doc:** the delivery breakdown — how we build it, in a Scrum-within-a-phased-roadmap model.
**Status:** Draft v1.0 · planning estimates, not commitments.

---

## 1. Delivery Model & Scrum Mechanics

Scrum has no native concept of "phases." We reconcile the user's request as follows:

- The **12 parts** are **Epics / release increments** — each is a vertically demoable slice of the platform.
- Each part is delivered across **one or more 2-week sprints**.
- Scrum artifacts and ceremonies operate *inside* every part. Phases give sequencing and dependency order; Scrum gives the cadence and feedback loop.

### Cadence & team (assumptions — adjust to reality)
| Item | Assumption |
|------|-----------|
| Sprint length | 2 weeks |
| Squads | 1–2 cross-functional squads (backend, AI/agents, data/retrieval, frontend, platform/SRE, security) |
| Estimation | Story points, Fibonacci (1,2,3,5,8,13); planning poker |
| Velocity | Calibrate after Sprint 2; re-baseline each part |
| Total estimate | ~27–28 sprints (~13 months) to GA — governance-first, narrow-deep before broad |

### Roles
- **Product Owner** — owns backlog, priorities (MoSCoW from PRD §33), acceptance.
- **Scrum Master** — facilitates ceremonies, removes blockers, guards WIP/limits.
- **Dev team** — the squad(s) above.
- **Stakeholders** — Principal Eng, AI Architect, Security, Compliance/Legal (gate reviewers per PRD).

### Ceremonies
Sprint Planning → Daily Standup → Backlog Refinement (mid-sprint) → Sprint Review/Demo → Retrospective. Quarterly: roadmap review + DR game-day.

### Definition of Ready (DoR) — a story may enter a sprint when:
- Mapped to ≥1 FR/NFR and a persona/user story.
- Acceptance criteria written (Given/When/Then where applicable).
- Dependencies resolved or stubbed; test approach known.
- Estimated and sized to fit one sprint.

### Definition of Done (DoD) — global, applies to **every** story:
- Code typed (mypy/pyright strict on core), async on IO paths, Pydantic at boundaries.
- Unit + integration tests pass; ≥80% coverage on core logic (NFR-11).
- Security: no secrets in code/logs, RBAC enforced on new boundaries, injection-safe (SEC-05/06).
- Observability: OTel spans + cost/token accounting on new hot-path calls (AP-9).
- Eval: new behavior covered by a grader where it affects answer quality/safety.
- Docs/ADR updated; OpenAPI current; merged via gated CI/CD.

### Cross-cutting tracks (run in **every** sprint, not a phase)
Reserve ~20% squad capacity for these — they are continuous and gate releases:
1. **Security** (SEC-01…10) — woven in; pen-test concentrated in Phase 12.
2. **Evaluation** (FR-054) — gold sets grow each phase; CI eval gate from Phase 1.
3. **Observability/Cost** (FR-055, NFR-09/10) — instrument as you build.
4. **Tech-debt / refactor** — keep clean architecture (AP-10).

### Release mapping (PRD §29)
| Release | Gate | Reached after |
|---------|------|---------------|
| **Alpha** (internal) | One investigation type works end-to-end with verification + audit; eval harness live | **Phase 7** |
| **Private Beta** (design partners) | ≥90% accuracy on partner gold set; RBAC + audit complete; approval workflow in use | **Phase 10** |
| **GA** | All Must reqs met; SLOs sustained 30 days; security review + compliance sign-off | **Phase 12** |
| **Post-GA** | Connector expansion, proactive alerts, scale targets | Roadmap (Phase 13+) |

---

## 2. The 12 Phases

> Template per phase: **Goal · Maps to · Epic · Representative stories · Key deliverables · Definition of Done (exit) · Dependencies · Estimate · Demo.**

---

### Phase 1 — Foundations & Developer Experience
**Goal:** Stand up the skeleton everything else plugs into; one request can flow through an empty pipeline, traced and deployed.
**Maps to:** PRD M0 · FR-001 (intake stub) · NFR-04/11/12 · TAD Ch 3, 15, 18 · AP-6/7/9/10.
**Epic:** Platform skeleton & CI/CD.
**Representative stories:** "As a dev, I can run the whole stack locally with one command." / "As ops, every request emits a trace id."
**Key deliverables:**
- Monorepo per TAD §18.3 (`gateway/`, `orchestrator/`, `agents/`, `ports/`, `adapters/`…).
- FastAPI API gateway + `/v1/runs` accept stub + SSE channel skeleton.
- Docker (multi-stage, non-root) + Docker Compose dev + base K8s/Helm manifests.
- CI/CD pipeline: lint → type-check → test → **eval-gate placeholder** → image scan → build/sign → staging.
- OpenTelemetry bootstrap; structured logging; correlation ids.
- LiteLLM abstraction (minimal) + provider config; eval harness skeleton + gold-set v1 seed.
**Exit / DoD:** A no-op run is accepted end-to-end, traced, and deployed to staging via CI/CD.
**Dependencies:** none (start).
**Estimate:** 2 sprints. **Demo:** trigger a run, watch it traced through to staging.

---

### Phase 2 — Identity, Access & Audit (Governance Core)
**Goal:** Make the platform governed *before* it is capable — deny-by-default identity, tenancy, and immutable audit.
**Maps to:** PRD M0 · FR-050, FR-051 · SEC-01…10 · NFR-07 · TAD Ch 11 · AP-5.
**Epic:** Authn/Authz + audit subsystem.
**Representative stories:** US-B2 (Omar wants a complete audit trail). "As Sara, access is governed automatically."
**Key deliverables:**
- SSO/OAuth (OIDC/SAML); short-lived service JWTs; httpOnly sessions.
- RBAC model (role → permission → resource/domain/effect); per-request permission resolution.
- Tenant isolation: `tenant_id` everywhere + PostgreSQL **Row-Level Security** policies.
- Managed vault integration; user-scoped token minting; no secrets in prompts/logs.
- Append-only `audit_event` (role + trigger enforced); authz-decision logging.
**Exit / DoD:** Cross-tenant access is structurally impossible (RLS proven by test); every authz decision and mutation is audited; secrets only in vault.
**Dependencies:** Phase 1.
**Estimate:** 2 sprints. **Demo:** two tenants, identical query, zero data bleed; audit log shows full lineage.

---

### Phase 3 — Data Stores & Document/Embedding Pipeline
**Goal:** Provision the polyglot persistence layer and turn raw documents into governed, embedded, ACL-tagged chunks.
**Maps to:** PRD M1 · FR-015, FR-016 · TAD Ch 4, Ch 7 (embeddings) · NFR-08 (PII).
**Epic:** Knowledge base ingestion.
**Representative stories:** "As the platform, I can ingest a PDF/Office/HTML/table doc into the KB with ACL tags and timestamps."
**Key deliverables:**
- PostgreSQL(+pgvector), Qdrant, Neo4j, Redis, object store — provisioned with schemas/migrations (expand/contract).
- Document pipeline: parse → chunk → embed (versioned models) → store with payload (source, `acl_tags`, `valid_from/to`).
- Qdrant per-tenant collections (dense + sparse/bm25 named vectors); payload indexes for ACL/time.
- Embedding cache by content hash.
**Exit / DoD:** A tenant corpus ingests cleanly; chunks carry ACL + time payload; PII redaction applied to stored artifacts.
**Dependencies:** Phase 2 (ACL tags need identity model).
**Estimate:** 2 sprints. **Demo:** ingest a doc set; inspect ACL/time-tagged vectors in Qdrant.

---

### Phase 4 — Retrieval Engine (Hybrid Search)
**Goal:** Identity-scoped, time-aware hybrid retrieval that never surfaces what the user can't see.
**Maps to:** PRD M1 · FR-010, FR-011, FR-012, FR-014 · TAD Ch 7.1 · AP-5.
**Epic:** Hybrid retrieval + reranking.
**Representative stories:** "As Priya, retrieval respects my permissions and the as-of date." / multi-query for complex questions.
**Key deliverables:**
- Dense ANN + sparse BM25; **ACL/metadata/time filter applied *before* fusion**; RRF fusion.
- Cross-encoder reranking; extractive+abstractive context compression.
- Multi-query expansion; adaptive effort levels (set later by Planner).
- Retrieval-result cache (short TTL, busted on corpus update).
**Exit / DoD:** Grounded retrieval over a tenant corpus; filtered docs provably never influence ranking; latency within budget.
**Dependencies:** Phase 3.
**Estimate:** 2 sprints. **Demo:** ask a knowledge question; ranked evidence with sources, ACL-respecting.

---

### Phase 5 — Analytics Engine (NL-to-SQL + BI)
**Goal:** Safe NL→SQL over governed warehouses, plus the statistics that turn numbers into explanations.
**Maps to:** PRD M2 · FR-020…024 · TAD Ch 8.1/8.2 · NFR-06.
**Epic:** Governed analytics.
**Representative stories:** US-A1 (driver-level "why" answer without writing SQL). US-B1 (number linked to its query).
**Key deliverables:**
- NL→SQL generation (structured); **AST validator**: read-only SELECT only, known columns, tenant predicate required, cost-guard, statement timeout + row cap; read-replica execution.
- Repair loop on validation/implausibility; SQL-result cache.
- BI: trend decomposition, variance/driver attribution, correlation (with multiple-comparison control), anomaly detection, basic forecasting w/ intervals — in-process (Polars/DuckDB).
- "Correlation ≠ causation" enforced in output labeling.
- Each result bound to its exact query (provenance seed).
**Exit / DoD:** A verified metric-movement explanation is produced; no non-read-only SQL can execute; every claim carries its query.
**Dependencies:** Phase 2 (RBAC/tenant predicate), Phase 3 (schemas).
**Estimate:** 3 sprints. **Demo:** "why did EMEA margin fall?" → ranked drivers with magnitudes + queries.

---

### Phase 6 — Orchestration Core (LangGraph)
**Goal:** The brain — interpret → plan → route → execute → reflect, durable and streamed, with hard resource bounds.
**Maps to:** PRD M3 · FR-002…006, FR-040 · TAD Ch 5, 6 · AP-6/7/8 · NFR-01/05.
**Epic:** Multi-agent orchestration.
**Representative stories:** US-A2 (review plan before execution). US-A3 (watch progress stream).
**Key deliverables:**
- Agents: Goal Interpreter, Planner, Router, Execution Coordinator, Reflection (typed I/O, prompt contracts).
- `RunState` (Pydantic, reducers for parallel branches); LangGraph assembly; **Postgres checkpointing**; resume-on-crash.
- Conditional edges: route fan-out, reflection loop, parallel safe branches.
- Resilience: retry+backoff (idempotency keys), iteration cap, loop-detection (plan-similarity), per-step + global timeouts, per-run cost ceiling.
- SSE streaming of step transitions / tool calls / partial findings (first token <3s).
**Exit / DoD:** A multi-step investigation runs, re-plans on insufficiency, streams progress, resumes after restart, and respects all bounds.
**Dependencies:** Phases 4 & 5 (executors to orchestrate).
**Estimate:** 3 sprints. **Demo:** complex question → plan preview → live streaming → re-plan loop → bounded finish.

---

### Phase 7 — Verification, Recommendation & Reporting  → 🏁 **ALPHA**
**Goal:** Make answers trustworthy: independently re-derive every number, attach confidence + provenance, produce the executive brief.
**Maps to:** PRD M4 · FR-041…045 · EXP-01…06 · TAD Ch 8.3 · AP-1/3.
**Epic:** Trust & output.
**Representative stories:** US-B1, US-B3 (confidence + assumptions), US-E1/E2 (executive brief + drill-down).
**Key deliverables:**
- **Verification Agent** — fresh context, re-executes source query / recomputes aggregation / cross-source corroboration; statuses: verified / unverified / conflicting (never silently reconciled).
- Calibrated confidence (not raw logprob); assumptions surfaced.
- Recommendation Engine: ranks actions by impact × confidence; consumes only verified/flagged claims.
- Executive report generator (layered: headline → drivers → evidence → raw queries); multi-format export.
- Explainability surfaces: plan, per-claim provenance + verification status, stated limitations.
- Short/long-term memory persistence of conversation/plan/findings (FR-045, minimal).
**Exit / DoD:** **Alpha gate** — one investigation type works end-to-end with independent verification + full audit; eval harness live and graded.
**Dependencies:** Phase 6.
**Estimate:** 2 sprints. **Demo:** full investigation → verified one-page brief with provenance + confidence.

---

### Phase 8 — MCP Integration & Tool Calling
**Goal:** The extensibility backbone — onboard any enterprise system as a governed tool, declaratively, no code deploy.
**Maps to:** PRD M5 · FR-030…033 · TAD Ch 9 · AP-4 · G4 (≤5 eng-days onboarding).
**Epic:** MCP-first integration.
**Representative stories:** US-D1 (register a system as an MCP server with declarative permissions).
**Key deliverables:**
- MCP Manager: register → discover (list tools, cache JSON schemas) → health-check → lifecycle (Healthy/Degraded/Failed/Draining) with circuit breaker + backoff.
- Per-tool permission descriptors (effect, domains, ACL); RBAC check pre-invocation (deny-by-default).
- Tool Agent: capability/schema matching; ties broken by health/latency/cost; typed-arg validation against schema.
- Fallback to alternate tool/path; narrowed-scope continuation (AP-8).
- Untrusted tool output treated strictly as data (SEC-06); idempotent-read caching.
**Exit / DoD:** A new MCP server registered via portal appears in discovery, is health-checked, and is invocable subject to RBAC — no code deploy; onboarding time tracked toward the ≤5-day KPI.
**Dependencies:** Phase 2 (RBAC), Phase 6 (orchestrator selects tools).
**Estimate:** 2 sprints. **Demo:** register a sample MCP tool live; orchestrator calls it within permissions.

---

### Phase 9 — Governed Autonomy (Human Approval & Safety)
**Goal:** Lock the safety guarantee — no write or high-impact action without explicit human approval.
**Maps to:** PRD M5 · FR-034 · SEC-07 · §24 (AI Safety) · TAD Ch 14.3 · AP-2 · G5 (zero unapproved writes).
**Epic:** Human-in-the-loop autonomy.
**Representative stories:** US-C1 (approval gate on write actions), US-C2 (autonomy policy configurable per action type).
**Key deliverables:**
- Approval node as LangGraph **`interrupt_before`**; checkpoint persisted on pause.
- Approval UI: shows proposed action + exact payload; approve → execute with user-scoped token; reject → skip + record.
- Per-action-type autonomy policy (read auto; write/high-impact gated).
- AI-safety bounds enforced & evaluated: bounded autonomy, grounding suppression of ungrounded claims, fail-safe partial answers, human override (stop/edit/reject), injection resistance — added to safety eval suite (target: 0 unapproved actions).
**Exit / DoD:** Any side-effecting step pauses, presents payload, and proceeds only on approval; rejection logged + skipped; safety suite shows zero unapproved-action attempts passing.
**Dependencies:** Phase 8.
**Estimate:** 1–2 sprints. **Demo:** plan that opens a ticket → pause → approve/reject → audited outcome.

---

### Phase 10 — GraphRAG & Knowledge Graph  → 🏁 **PRIVATE BETA**
**Goal:** Answer structural "why" questions via metric lineage the graph encodes explicitly.
**Maps to:** PRD M6 · FR-013 · TAD Ch 4.4, 7.2 · G2 (accuracy).
**Epic:** Graph-augmented retrieval.
**Representative stories:** Multi-hop driver analysis ("margin ← revenue, COGS, sliced by region; what events impacted it?").
**Key deliverables:**
- Neo4j meta-model (Metric/Dimension/DataSource/Entity/Event/Doc/Team) + uniqueness constraints.
- Entity/relation extraction; metric-lineage ingestion (with driver weights/direction).
- Bounded multi-hop traversal (Cypher); relevant-subgraph extraction; linearize-to-evidence; merge with vector hits.
- GraphRAG wired into the Retriever Agent + Planner effort levels.
**Exit / DoD:** **Private Beta gate** — driver analysis via lineage traversal works; ≥90% accuracy on partner gold set; RBAC + audit + approval workflow all in production use with design partners.
**Dependencies:** Phases 4, 6, 7.
**Estimate:** 2 sprints. **Demo:** root-cause question answered by graph traversal + vector evidence, verified.

---

### Phase 11 — Platform Services & Administration
**Goal:** Operationalize governance, memory, and admin so the platform is configurable, learns with use, and stays cost-bounded.
**Maps to:** PRD M3/M7 spread · FR-045 (full), FR-052, FR-053, FR-056, FR-057 · TAD Ch 10, 14 · NFR-10/13.
**Epic:** Admin & platform services.
**Representative stories:** US-D2 (manage/rollback prompt versions), US-D3 (per-tenant budgets + routing), Lena/Omar admin flows.
**Key deliverables:**
- Memory architecture (working/episodic/semantic) + Memory Agent consolidation + governance (no PII to shared semantic memory; erasure cascade).
- **Prompt management**: versioned/immutable artifacts, eval-gated promotion, canary + one-click rollback.
- **Model routing** (full policy): per-task tiering; verification/planning pinned to strong models; budget-aware.
- Notification system (completion/approval/anomaly via channels).
- **Administration Portal**: tenants, users, roles, connectors, budgets, feature flags, prompt/model governance.
- Platform services: caching layers, queue partitioning (KEDA), rate limiting, backpressure; frontend polish (layered answers, provenance, accessibility WCAG 2.1 AA).
**Exit / DoD:** Admins self-serve RBAC/prompts/routing/budgets; memory improves repeat questions without leaking; per-run cost capped & tracked; UI meets WCAG 2.1 AA.
**Dependencies:** Phases 2, 6, 7, 8.
**Estimate:** 3 sprints. **Demo:** admin rolls back a prompt + sets a tenant budget; repeat question reuses a prior plan.

---

### Phase 12 — Hardening, Scale, Compliance & GA  → 🏁 **GA**
**Goal:** Prove it at scale, secure it, evidence compliance, and pass the GA gate.
**Maps to:** PRD M7 · NFR-02/03/04/06 · §16 (Scaling), §20–22 (Sec/Privacy/Compliance), §23 (Scalability) · TAD Ch 16 · all "Must".
**Epic:** Production hardening.
**Representative stories:** Omar signs off audit/compliance; ops sustains SLOs; security clears pen-test.
**Key deliverables:**
- Load/perf test to NFR-02 (200 concurrent/cluster); autoscaling (HPA/KEDA) validated; no single-node hot-path bottleneck.
- Reliability: idempotency, circuit breakers, graceful degradation, checkpoint/resume — all proven under fault injection.
- **Disaster recovery**: WAL+snapshots, cross-AZ/region replication (RPO ≤5min), IaC restore (RTO ≤1hr), quarterly game-day.
- Data residency per tenant; retention/erasure; PII redaction audited.
- Full eval suite as **hard CI gates** (accuracy ≥92%, verified coverage ≥95%, grounding, safety, no unapproved actions).
- Security: SAST/DAST, dependency/container scanning, **penetration test**; compliance evidence (SOC2/GDPR/HIPAA/SOX/ISO27001/EU AI Act as applicable per vertical).
**Exit / DoD:** **GA gate** — all Must requirements met; SLOs sustained 30 days; security review + per-vertical compliance sign-off complete.
**Dependencies:** all prior phases.
**Estimate:** 3 sprints. **Demo:** load test at target concurrency; DR game-day; green compliance/eval gates.

---

## 3. Sequencing & Dependency View

```
Governance-first foundation        Intelligence core            Integration & trust        Productionize
┌───────────────┐  ┌────────────┐  ┌──────────┐ ┌────────────┐  ┌──────┐ ┌──────────┐    ┌────────┐ ┌──────────┐ ┌──────────┐
│1 Foundations  │→ │2 Identity/ │→ │3 Data &  │→│4 Retrieval │   │6 Orch│ │7 Verify+ │ →  │8 MCP & │ │9 Approval│ │11 Platform│
│  & DevEx      │  │  Audit     │  │  Doc Pipe│ │  (Hybrid)  │ ↘ │ Core │↗│  Recommend│    │  Tools │→│  & Safety│ │  & Admin  │
└───────────────┘  └────────────┘  └──────────┘ └────────────┘   └──────┘ │  (ALPHA) │    └────────┘ └──────────┘ └──────────┘
                                    └──────────┘ ┌────────────┐ ↗         └──────────┘         │           │            │
                                                 │5 Analytics │                          ┌──────────┐      │            ▼
                                                 │ (NL-SQL+BI)│                          │10 GraphRAG│ ◄────┘      ┌──────────┐
                                                 └────────────┘                          │ (BETA)    │            │12 Harden │
                                                                                         └──────────┘            │ +GA      │
                                                                                                                 └──────────┘
```
- Phases 4 & 5 can run **in parallel** (different squads) once Phase 3 lands.
- Phase 6 needs 4+5 as executors.
- Phases 8 & 9 are tightly coupled (tooling then its safety gate).
- Phase 11 can begin partially in parallel with 8–10 (memory/admin don't block the agent path).

## 4. Risk Burndown (PRD §34 mapped to phases)
| Risk | Score | Burned down in |
|------|-------|----------------|
| R1 Wrong answer erodes trust | 15 🔴 | Phase 7 (verification), Phase 12 (eval gates) |
| R3 Prompt injection | 12 🔴 | Phases 2, 8 (data-not-instructions), continuous safety evals |
| R2 Cross-permission leakage | 10 🟠 | Phase 2 (RLS), Phase 4 (pre-fusion ACL) |
| R8 Compliance gap | 10 🟠 | Phase 12 (per-vertical gate) |
| R4 Runaway cost | 9 🟠 | Phase 6 (bounds), Phase 11 (routing/budgets) |
| R7 Adoption resistance | 9 🟠 | Phase 7/9 (analyst as approver), continuous UX |
| R5 MCP not cheap | 8 🟠 | Phase 8 (declarative onboarding + KPI) |
| R6 Provider outage | 8 🟠 | Phase 1/11 (LiteLLM routing + fallback) |

## 5. Backlog Seeding (next steps)
1. Create the **Product Backlog**: import FR-001…057 as epics→stories, tag with MoSCoW + phase + persona.
2. Stand up board (e.g., Jira/Linear) with the 12 phases as epics and the cross-cutting tracks as swimlanes.
3. **Sprint 0**: environments, repo, CI/CD scaffold, gold-set v1 seed, team norms + DoR/DoD ratified.
4. Calibrate velocity over Sprints 1–2; re-baseline estimates per phase.

---
*Living artifact — revise as velocity and scope clarify. Pairs with EADIP PRD + TAD.*
