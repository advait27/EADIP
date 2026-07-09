# 7. Phase 6 — orchestration core

Date: 2026-06-26

## Status
Accepted

## Context
Phase 6 is the brain: interpret → plan → route → execute → reflect, durable and
streamed, with hard resource bounds (PRD M3; FR-002..006, FR-040; TAD Ch 5/6;
AP-6/7/8; NFR-01/05). Exit bar: a multi-step investigation runs, re-plans on
insufficiency, streams progress, resumes after restart, and respects all bounds.

## Decisions
1. **A purpose-built deterministic engine implementing the LangGraph pattern —
   not the LangGraph dependency.** The engine is a typed state-graph driver
   (interpret/plan/route/execute/reflect nodes, conditional edges for the
   reflection loop and safe parallel branches). It is built behind clean
   agent/executor/checkpointer seams so the whole brain is offline-deterministic
   and unit-testable, avoids a heavy dependency on bleeding-edge Python 3.14, and
   keeps orchestration fully governed. This is consistent with every prior phase
   (deterministic core, real backends behind ports). A LangGraph assembly can
   slot in behind the same seams later; the trade-off is we maintain the graph
   driver ourselves rather than inheriting LangGraph's ecosystem.
2. **Typed agents with deterministic defaults + LLM seams.** Goal Interpreter,
   Planner, and Reflection each have a heuristic default (offline, testable) and
   an LLM variant behind the same Protocol that falls back to the heuristic on
   unparseable output. The Router is purely deterministic (dependency-aware
   batching). Typed Pydantic I/O doubles as the prompt contract and the wire/
   checkpoint schema.
3. **`RunState` is the single Pydantic state with a reducer.**
   `apply_step_results` merges a (possibly parallel) batch in a deterministic id
   order, so concurrent branches never race shared state. The same object
   serialises into the checkpoint and over SSE.
4. **Checkpoint after every node → resume-on-crash.** A `Checkpointer` port
   (in-memory default, RLS-scoped Postgres adapter, migration 0005) persists
   `RunState`. Resume is natural: a loaded checkpoint with a goal+plan skips
   interpretation and, because completed step ids are persisted, re-execution
   skips finished steps (idempotency by state, not a side cache).
5. **Hard bounds, all enforced and observable.** Iteration cap, per-run cost
   ceiling, global deadline (all from the existing run guards), per-step timeout,
   retry+backoff, and plan-similarity loop detection. A breached bound stops the
   run gracefully with a `stop_reason` and a `bound.stop` event; partial findings
   are kept (status `done` if any, else `failed`).
6. **The SSE stream *is* the orchestration.** `GET /v1/runs/{id}/events` loads
   the run's (tenant-scoped) checkpoint and runs the engine, relaying
   `goal.interpreted` / `plan.created` (the plan preview, US-A2) / `step.*` /
   `finding.partial` / `reflection` / `bound.stop` / `run.done`. RBAC
   (`run/runs` read) + audit (`run.create`, `run.stream`) apply, and a dropped
   connection can resume from the last checkpoint.

## Consequences
- A complex question produces a streamed, multi-step investigation: plan preview,
  parallel retrieve+analyze, ranked analytics findings + grounded passages, a
  reflection, and a bounded finish — demonstrated end-to-end (clean sufficient
  finish with grounding; re-plan→loop-detected bounded stop on an empty corpus;
  fresh-engine resume from a mid-run checkpoint). All bounds proven by unit tests.
- The engine consumes Phase 4 retrieval and Phase 5 analytics as executors; Phase
  7 (Verification/Recommendation) re-derives the findings the engine collected,
  and Phase 9 (HITL) turns the plan-preview event into an approval gate.
- We own the graph driver (≈250 lines) instead of LangGraph; if its ecosystem
  (visual debugging, prebuilt checkpointers) becomes worth it, swap it in behind
  the agent/executor/checkpointer seams without touching the agents or the route.
- Findings carry provenance pointers (passage source refs, exact SQL) but are not
  yet independently re-verified — that is Phase 7. Reflection is heuristic
  coverage, not a correctness check.
