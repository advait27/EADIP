# 9. Phase 8 — MCP integration & tool calling

Date: 2026-07-04

## Status
Accepted

## Context
Phase 8 is the extensibility backbone (PRD M5; FR-030..033; TAD Ch 9; AP-4/8;
G4 ≤5-eng-day onboarding): onboard any enterprise system as a *governed* MCP
tool, declaratively and with no code deploy, and let the orchestrator select and
call it within the same RBAC/audit envelope as everything else. Exit bar: a
server registered via the portal appears in discovery, is health-checked, and is
invocable subject to RBAC — no code deploy.

## Decisions
1. **Ports-and-adapters, in-process default (as Phases 4/5/6).** A `ToolTransport`
   Protocol abstracts the wire; the default `InProcessTransport` hosts tools as
   async Python callables, so the whole stack is offline-deterministic and
   unit-testable. Real stdio/HTTP MCP transports slot in behind `build_transport`
   (lazy import, MCP SDK optional). No new heavyweight dependency was added.
2. **The MCP Manager owns the lifecycle.** register → discover (list tools, cache
   their JSON schemas) → health-check → lifecycle (Healthy/Degraded/Failed/
   Draining) with a **circuit breaker + exponential backoff**. A run of failures
   trips the breaker OPEN (fail fast); a post-cooldown probe re-closes it;
   sustained latency degrades (still invocable, de-prioritised). Clock-injected,
   like the orchestrator, so it is deterministic in tests.
3. **Governance is enforced at invocation, deny-by-default.** Every call goes
   RBAC pre-check → typed-arg gate → breaker → transport → cache. RBAC reuses the
   Phase 2 PDP against each tool's **declarative permission descriptor** (effect +
   data domains + required ACL tags); read-only tools are a read tier (analysts),
   write/high-impact stay approver-only (the Phase 9 approval seam). The **typed-arg
   gate** is the tool-calling analogue of the SQL safety gate: arguments are
   validated + sanitised against the cached JSON-Schema (unknown fields rejected,
   types coerced narrowly, enum/bounds enforced) before the connector is touched.
4. **Untrusted output is data, never instructions (SEC-06).** Tool output is
   wrapped in an `{untrusted: true, source: "tool_output", data: …}` envelope, so
   nothing downstream can treat a connector's response as a command. Idempotent,
   read-only tool results are cached on (tenant, server, tool, args); side-effecting
   tools are never cached.
5. **The Tool Agent matches capability, the executor falls back (AP-8).** Given an
   intent, the Tool Agent ranks capable tools by content-token overlap, breaking
   ties by health → latency → cost, and returns an ordered candidate list. The
   `ToolExecutor` (a `kind="tool"` orchestrator executor) invokes the top choice
   and, on transport/circuit failure, falls back to the next — narrowed-scope
   continuation. A newly-registered tool becomes an orchestrator capability with
   **no code change**; the heuristic planner emits a tool step only on an explicit
   external-data signal *and* when it can supply the required argument (it never
   calls a tool blind; an LLM planner can fill richer arguments behind the seam).
6. **Verification treats tool results as provenance-verified, not re-derived.** A
   governed tool result enters the executive brief as a `tool_provenance` claim:
   its status is verified when it carries an auditable provenance pointer
   (server.tool), but the verifier does **not** independently re-derive an
   external system's response — that limitation is stated in the brief and the
   confidence is not inflated.
7. **Registry persistence mirrors the checkpointer.** In-memory for dev/tests; a
   Postgres store with **RLS FORCE** (migration 0006) for prod, so one tenant can
   never see or invoke another's connectors. Connector credentials are resolved
   from the vault by `secret_ref` (SEC-05) and passed to the transport — never
   stored inline, returned, or logged.

## Consequences
- Onboarding a system is an `upsert` in the registry via `POST /v1/tools` (RBAC
  `tool/*/write`, audited) — no deploy. It immediately appears in `GET /v1/tools`
  discovery with cached schemas + health, is health-checkable, and its tools are
  invocable subject to RBAC. Demonstrated live: analyst reads `fx_rate` (allowed,
  cached), analyst is denied `open_ticket` (403, write), approver may call it; a
  bad enum is refused as `invalid_arguments`; the orchestrator plans and calls the
  tool within permissions and surfaces the result in the verified brief.
- The circuit breaker means a sick connector fails fast instead of stalling a run;
  the executor's fallback means a single denied/failed tool doesn't sink a step.
- Verification is only as strong as re-derivation allows: external tool outputs are
  trusted as *data with recorded provenance*, not independently recomputed — so a
  wrong-but-well-formed connector response is not caught here (it is auditable and
  flagged as untrusted). The capability matcher is token-overlap, not semantic;
  an LLM matcher fits behind the same `select` signature.
- Real stdio/HTTP MCP transports and the ≤5-day onboarding KPI instrumentation are
  the remaining production work; the governed path they plug into is complete.
- Phase 9 (Governed Autonomy) turns the write/high-impact deny into an explicit
  human-approval gate (`interrupt_before`), building directly on the permission
  descriptors and the RBAC boundary established here.
