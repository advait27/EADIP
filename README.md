# EADIP — Enterprise Autonomous Decision Intelligence Platform

An AI system that investigates business questions the way a careful analyst
would — it plans, gathers evidence, runs the numbers, **double-checks every
claim**, and asks a human before it touches anything — built for enterprises
where trust, security and auditability are non-negotiable.

---

## What is this? (no AI background needed)

Imagine you run a company and ask: **"Why did our profit margin in Europe fall
last quarter?"**

Normally, a data analyst would spend hours or days on that: pulling reports,
querying databases, cross-checking numbers, and writing a summary. EADIP does
that job automatically, in minutes — and, crucially, it **shows its work**:

1. **It understands the question.** "Profit margin", "Europe", "last quarter"
   become a concrete investigation goal.
2. **It makes a plan.** For example: *find relevant documents → query the
   financial database → compare the numbers → explain the change.* You can
   watch the plan and its progress live, step by step.
3. **It gathers evidence.** It searches your company's documents and knowledge
   base — but only the documents *you* are allowed to see.
4. **It runs the numbers.** It translates your question into database queries,
   ranks what drove the change ("Hardware costs in EMEA caused 96% of the
   decline"), spots anomalies, and attaches the *exact query* behind every
   number so anyone can check it.
5. **It double-checks itself.** A separate verification step re-runs every
   query and independently recomputes every figure. Numbers that check out are
   marked *verified*; disagreements are flagged *conflicting* — never quietly
   papered over.
6. **It reports like an executive briefing.** A one-line headline, the key
   findings with confidence levels, recommended actions, stated assumptions and
   limitations, and a full drill-down for the skeptics.
7. **It never acts alone.** If the investigation suggests *doing* something —
   say, opening a ticket in another system — it stops and asks a human for
   approval first. Reading is autonomous; changing anything requires a person.

And everything — every answer, every permission check, every approval, every
piece of removed personal data — is written to a tamper-evident **audit log**,
so compliance teams can reconstruct exactly what happened and why.

### Why is that hard?

Large language models (the AI behind chatbots) are great at sounding right and
notoriously capable of being wrong. In an enterprise, "sounds right" is not
good enough. EADIP's whole design answers one question: **how do you let an AI
investigate real company data without ever having to just take its word for
it?** The answers: independent verification of every number, provenance on
every claim, humans approving every action, hard cost/time limits on every
run, and strict walls between different companies' (tenants') data.

### Plain-English glossary

| Term you'll see | What it means here |
|---|---|
| **Tenant** | One customer organization. Every piece of data is walled off per tenant — enforced in the database itself, not just in the app. |
| **RBAC (roles)** | Who may do what. An *analyst* can investigate; an *approver* can authorize actions; an *admin* runs the platform. Everything else is denied by default. |
| **Retrieval / RAG** | Searching the company's own documents so answers are grounded in real sources instead of the AI's imagination. |
| **NL→SQL** | Turning a plain-English question into a database query — behind a safety gate that only permits read-only, tenant-scoped queries. |
| **Orchestrator** | The "project manager" engine that plans an investigation, runs the steps, reflects on whether the evidence is sufficient, and re-plans if not. |
| **Verification** | An independent re-computation of every number before it reaches the report. |
| **Provenance** | The receipt attached to every claim: the exact query or document it came from. |
| **MCP tools** | A standard way to plug other enterprise systems (ticketing, FX rates, CRMs…) into the AI — each behind its own permission checks. |
| **Human-in-the-loop** | Any action with side effects pauses and waits for a person to approve, reject, or edit it. |
| **Audit log** | An append-only record of every decision and action, for compliance. |
| **Checkpoint** | A saved snapshot of a running investigation, so a crash resumes where it left off instead of starting over. |

---

## Quickstart

```bash
# 1. Install deps (uv: https://docs.astral.sh/uv/)
make install            # uv sync --extra dev
uv sync --extra dev --extra data   # + DuckDB etc. for analytics/eval

# 2. Run the gateway locally (no external services needed — in-memory defaults)
make dev                # uvicorn eadip.gateway:app --reload --port 8000

# 3. Ask it something
curl -XPOST localhost:8000/v1/runs -H 'content-type: application/json' \
  -H 'X-Roles: analyst' -d '{"question":"Why did EMEA gross margin fall last quarter?"}'
# -> 202 { "id": "...", "status": "queued", "events_url": ".../events" }

curl -N localhost:8000/v1/runs/<id>/events -H 'X-Roles: analyst'  # live progress (SSE)
curl localhost:8000/v1/runs/<id>/report -H 'X-Roles: analyst'     # the verified brief

# 4. Quality gates (the same ones CI enforces)
make test lint type     # pytest + ruff + mypy
make eval               # live eval: accuracy / verified-coverage / grounding
make safety             # AI-safety suite: zero unapproved actions
make reliability        # fault-injection suite: crash/resume, breakers, bounds

# 5. Full local stack (gateway + Postgres + Redis + Qdrant + Neo4j + OTel)
make up                 # docker compose up --build
```

OpenAPI docs are served at `http://localhost:8000/docs`. Everything runs
offline by default (deterministic in-memory backends); production backends
(Postgres, Qdrant, Redis, Neo4j, real LLMs) are switched on by settings — no
code changes.

---

## The platform in detail

Each capability below shipped as one of 12 delivery phases; every phase has an
Architecture Decision Record in [docs/adr/](docs/adr/) explaining the *why*.

### Identity, access & audit
Two auth modes (`EADIP_AUTH_MODE`): `dev` trusts `X-Tenant-ID`/`X-User-ID`/`X-Roles`
headers for local work; `oidc` verifies a Bearer JWT against your identity
provider. Authorization is **deny-by-default** RBAC enforced and audited at
every route. Tenant isolation is enforced in Postgres itself with **Row-Level
Security (FORCE)** on every tenant table, and the audit log is append-only.
```bash
docker compose up -d postgres && uv run eadip-migrate
EADIP_DATABASE_ENABLED=true make dev
```

### Knowledge ingestion
Parse → **redact personal data (PII)** → chunk → embed (cache-first) → upsert
into a per-tenant vector store. Every chunk carries access-control tags and a
validity window, so retrieval is identity-scoped and time-aware. PII redaction
is itself audited (what types were found and where — never the values).
```bash
uv run eadip-ingest <tenant-uuid> ./report.html --acl finance,emea
```

### Searching the knowledge base
`POST /v1/search` runs hybrid dense+sparse retrieval with rank fusion,
multi-query expansion, reranking and context compression. ACL and time filters
apply **before** ranking — a user never sees, or is influenced by, documents
outside their access.

### Governed analytics (NL→SQL + BI)
`POST /v1/analytics` answers metric-movement questions. Every candidate query
must pass an **AST safety gate** before a single row is read: read-only
`SELECT` only, known schema tables/columns only, allow-listed functions, a
required tenant filter, no `UNION`/`OR`, and a join cost guard. Results include
ranked drivers with magnitudes, anomaly/forecast/correlation findings
(correlations are labelled *association, not causation*), and the exact SQL
behind every claim.

### Orchestrated investigations
`POST /v1/runs` + the SSE stream *is* the orchestration: a deterministic
interpret → plan → route → execute → reflect engine that grounds with
retrieval, quantifies with analytics, re-plans when evidence is insufficient,
runs independent steps in parallel, **checkpoints after every node** (a crash
resumes, never restarts), and enforces hard bounds: iteration cap, per-run cost
ceiling, deadline, per-step timeout, and loop detection. Purpose-built
LangGraph-pattern engine — no framework dependency (see
[ADR-0007](docs/adr/0007-phase6-orchestration-core.md)).

### Verified reporting
On completion, a Verification Agent re-runs each finding's source query and
**independently recomputes** the number: matches are `verified`, disagreements
`conflicting` (never silently reconciled), un-runnable ones `unverified`.
Claims carry calibrated confidence + provenance; recommendations are ranked by
impact × confidence from verified, non-association claims only.
`GET /v1/runs/{id}/report` returns the layered brief.

### Governed tools (MCP)
Onboard any enterprise system as a governed tool — declaratively, no code
deploy. Registration discovers the server's tools and caches their JSON
Schemas; a circuit breaker + backoff manages health. Every invocation passes a
pre-invocation RBAC check, a **typed-argument gate** (validate + sanitise
against the schema; extra fields rejected), and returns its output wrapped as
**untrusted data** — a tool's response can never smuggle instructions into the
AI. The orchestrator can use any registered tool as a plan step, with fallback.

### Human approval (governed autonomy)
The safety guarantee: **no write or high-impact action executes without
explicit human approval.** A side-effecting step pauses the run (checkpointed),
presents its exact payload, and waits. Approve / reject / edit-then-approve via
the API; approval requires the same grant that gates the action itself, and the
action executes under the **approver's** authority. An AI-safety suite
(`make safety`) asserts six bounds — bounded autonomy, grounding suppression,
fail-safe partial answers, human override, injection resistance, memory
governance — with zero unapproved actions, as a blocking CI gate.

### GraphRAG — metric lineage
A typed knowledge graph encodes how metrics relate: `gross_margin` is
`DERIVED_FROM` revenue (+) and cogs (−), `SLICED_BY` region/product, with
business **Events** that `IMPACTS` metrics. Deep "why" questions traverse this
lineage (bounded, ACL-scoped) and merge the findings with document search — so
"why did margin fall?" surfaces the *driving event*, not just similar text.
In-memory graph by default; Neo4j behind the same interface.

### Memory, prompts, routing, budgets & the admin portal
- **Memory:** finished runs are consolidated — a repeat question (even
  rephrased) reuses its prior plan (still fully governed); verified claims
  become shared facts. PII never enters shared memory, and one audited admin
  call erases a tenant's memory entirely (GDPR).
- **Prompt management:** the AI's instructions are versioned, immutable,
  sha-sealed artifacts. A new version cannot serve traffic until an evaluation
  score passes the threshold; canary a fraction of traffic deterministically;
  one-click rollback. Changes apply live — no redeploy.
- **Model routing:** each task type gets a model tier; **planning and
  verification are pinned to the strongest tier** (downgrades refused), the
  rest degrade gracefully when budget is tight.
- **Budgets:** per-tenant monthly caps; every run charges its actual tracked
  cost; an exhausted cap refuses new runs (429).
- **Notifications:** approval-required / run-completed / anomaly / budget
  events fan out to inbox, log, or webhook channels.
- **Rate limiting + backpressure:** per-tenant token buckets and stream
  concurrency caps shed excess load early with `Retry-After`.
- **Administration portal** (`/v1/admin/*`, admin-only, every mutation
  audited): roles, prompts, routing, budgets, feature flags, memory, residency,
  notifications.

### Hardening, scale & compliance
- **Fault-injection reliability suite** (`make reliability`, 6/6): crash →
  resume with exactly-once steps; idempotent replay; retry recovery; breaker
  trip + half-open recovery; graceful degradation; bounded runaway cost.
- **Load gate** (`make load`): drives the real app at one replica's share
  (100 users) of the 200-per-cluster target — 0% errors, read-path p95 < 2 s.
  Building it caught and fixed a real bottleneck (analytics queries were
  serialized behind one lock; now MVCC-concurrent).
- **Eval hard gate:** accuracy ≥ 92%, verified coverage ≥ 95%, grounding ≥ 95%
  (measured 100/100/100 on the alpha gold set) — CI fails below.
- **Retention** (`eadip-retention`): expiry sweeps per data class; the audit
  trail is exempt by design. **Residency:** a tenant pinned to a region is
  refused (HTTP 451) by deployments elsewhere, before any data access.
- **Security:** bandit SAST + pip-audit + trivy image scan, all blocking in CI;
  pen-test scope in [docs/security/](docs/security/pen-test-scope.md).
- **Disaster recovery:** WAL archiving (RPO ≤ 5 min), IaC point-in-time restore
  (RTO ≤ 1 hr), quarterly game-day — [docs/runbooks/](docs/runbooks/disaster-recovery.md).
- **Compliance:** every control mapped to SOC 2 / GDPR / ISO 27001 / EU AI Act
  in [docs/compliance/controls-matrix.md](docs/compliance/controls-matrix.md).

---

## Architecture & repository layout

Clean architecture / ports-and-adapters throughout: business logic depends on
interfaces (`ports/`), and swappable `adapters/` implement them. Dev/test
defaults are deterministic and in-process (no external services, no network);
production backends are selected by settings.

```
src/eadip/
  gateway/        FastAPI app, routes (/v1/*), auth, authz, middleware
  config/         pydantic-settings (env-driven, safe defaults)
  observability/  structlog + OpenTelemetry (no-op unless enabled)
  domain/         framework-free entities (Run, RunStatus)
  ports/          interfaces: VectorStore, Warehouse, KnowledgeGraph, ...
  adapters/       Postgres/Qdrant/Redis/Neo4j/DuckDB/LiteLLM + sql/ migrations
  security/       Identity, RBAC, PDP, audit, redaction, vault, OIDC
  ingestion/      parse, PII redaction, chunk, embed pipeline, CLI
  retrieval/      hybrid search, RRF fusion, multi-query, rerank, compression
  analytics/      NL→SQL safety gate, BI stats, generator, cache, service
  orchestrator/   RunState, graph engine, executors, resilience, checkpointer
  agents/         goal interpreter, planner, router, reflection
  verification/   verifier (re-derive), confidence, recommend, brief
  mcp/            tool registry, transports, typed-arg gate, breaker, executor
  approval/       autonomy policy + human-approval service
  graph/          GraphRAG: lineage meta-model, traversal, linearization
  memory/         episodic + semantic long-term memory, Memory Agent
  platform/       prompts, routing, budgets, notifications, flags,
                  rate limiting, residency, retention
  eval/           eval harness + gold sets, safety suite, reliability suite,
                  load harness
tests/            unit/  integration/  (Postgres/Qdrant tests run in CI)
deploy/           helm/  k8s/ (KEDA, DR jobs)  docker/  terraform/  otel/
docs/             adr/ (13 ADRs)  runbooks/  compliance/  security/
```

**Stack:** Python 3.13+, FastAPI, Pydantic v2, `uv`; sqlglot (SQL safety gate);
DuckDB/Postgres warehouses; Qdrant vectors; Neo4j graph; Redis cache; LiteLLM
model seam; structlog + OpenTelemetry; pytest/ruff/mypy/bandit.

**CLIs:** `eadip-eval`, `eadip-safety`, `eadip-reliability`, `eadip-load`,
`eadip-retention`, `eadip-migrate`, `eadip-ingest`.

---

## Quality gates (all blocking in CI)

| Gate | What it proves |
|---|---|
| ruff + mypy | style + static type safety (158 source files) |
| pytest | 274 unit/integration tests (RLS + vector isolation run against real services in CI) |
| `eadip-eval` | live pipeline accuracy ≥ 92%, verified coverage ≥ 95%, grounding ≥ 95% |
| `eadip-safety` | six AI-safety bounds, zero unapproved actions |
| `eadip-reliability` | six fault-injection scenarios (crash/resume, breakers, degradation, bounds) |
| `eadip-load` | concurrency target at 0% errors, read-path p95 < 2 s |
| bandit + pip-audit + trivy | SAST, dependency CVEs, container CVEs |

## Status

- **All 12 delivery phases complete** — the GA engineering gate is GREEN.
- **Remaining for GA sign-off (operational, outside this repo):** a 30-day
  sustained SLO window in production ([SLOs](docs/runbooks/slos.md)), the
  external penetration test ([scope](docs/security/pen-test-scope.md)), a
  cross-region DR game-day on real infrastructure
  ([runbook](docs/runbooks/disaster-recovery.md)), and per-vertical compliance
  sign-off ([matrix](docs/compliance/controls-matrix.md)).
- Delivery plan: [EADIP_SCRUM_PLAN.md](EADIP_SCRUM_PLAN.md) · Requirements:
  `EADIP_PRD.docx` · Architecture: `EADIP_TAD.html` · Decisions:
  [docs/adr/](docs/adr/).

## Conventions

- Python 3.13+, async-first, fully typed; Pydantic at API boundaries,
  dataclasses in the domain; ports-and-adapters everywhere.
- Deterministic offline defaults: unit tests and demos need no external
  services; heavy backends are lazy imports behind settings flags.
- Secrets via vault references in production — `.env` is local-dev only.
- Every phase ends verified GREEN (all gates), demoed live, and documented in
  an ADR before the next begins.
