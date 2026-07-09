# 10. Phase 9 — governed autonomy (human approval & safety)

Date: 2026-07-06

## Status
Accepted

## Context
Phase 9 locks the safety guarantee (PRD M5; FR-034; SEC-07; §24 AI-Safety; TAD
Ch 14.3; AP-2; G5 zero unapproved writes): no write or high-impact action runs
without explicit human approval. Exit bar: any side-effecting step pauses,
presents its exact payload, and proceeds only on approval; rejection is logged +
skipped; the safety suite shows zero unapproved-action attempts passing.

## Decisions
1. **The engine is the approval node (interrupt_before).** Our deterministic
   state-graph engine gains an `interrupt_before` equivalent: before executing a
   batch, each step's executor is asked to `preflight` — describe the exact
   `ProposedAction` (tool + effect + payload) it would take. The `AutonomyPolicy`
   decides autonomy per effect (reads auto; writes/high-impact gated,
   deny-by-default). A gated, un-approved step is held back; independent runnable
   steps in the same batch still execute; once only gated work remains the run
   **pauses** — status `AWAITING_APPROVAL`, a pending `ApprovalRequest` recorded,
   `approval.required` streamed, and the stream ends. The full `RunState` is
   already checkpointed, so the pause is durable (resume-on-crash covers it too).
2. **Approval is out-of-band; resume flows through the normal governed path.**
   `POST /v1/runs/{id}/approvals/{aid}` applies a decision (approve / reject /
   edit-then-approve) to the durable ledger; the client then re-opens the SSE
   stream to resume from the checkpoint. The engine re-routes, sees the step is
   approved, and executes it — so an approved action still passes the Phase 8
   RBAC + typed-arg + breaker gates. No separate execution path exists to bypass.
3. **Approve executes under the approver's authority (user-scoped token).** The
   requester (e.g. an analyst) may not hold the write grant; the decision records
   the approver's roles and the approved step executes with *them*, so the tool's
   RBAC pre-check passes for exactly the human who authorized it — not the
   requester, and not ambiently.
4. **Who may approve = who may act.** Authorizing a side-effecting action requires
   the same `tool/*/write` grant that gates the action itself (the `approver`
   role). An analyst can *see* pending approvals (read) but cannot rule on them.
5. **Reject skips + records; edit amends the payload.** A rejected step is marked
   and skipped (a non-failing governed outcome), never executed; the run
   continues and completes. Edit-then-approve executes the operator-amended
   payload (recorded either way). A decided request cannot be re-decided — a
   double-submit is refused (409), so an action can never double-execute.
6. **AI-safety suite as a gate (`eadip-safety`).** Five bounds, each exercised
   against the real pipeline: bounded autonomy (0 unapproved actions), grounding
   suppression (a no-provenance claim is never surfaced as verified), fail-safe
   partial answers (a bounded run still returns a coherent brief, never a crash),
   human override (reject → 0 writes; approve → exactly 1), and injection
   resistance (an instruction embedded in tool output stays untrusted data — it
   only ever becomes a finding, never a plan/step). A non-zero exit fails CI.
7. **The planner never fabricates a write.** The heuristic planner emits a
   side-effecting `open_ticket` step only on an explicit operator "open a ticket"
   ask; that step then pauses for approval. Autonomy is bounded by construction.

## Consequences
- A remediation run (analyze → open a ticket) streams its analysis, then pauses
  with a pending approval carrying the exact ticket payload. An analyst is
  refused approval (403); an approver approves and re-opens the stream, and the
  ticket is opened under the approver's identity and lands, verified, in the
  brief. Rejecting instead skips the write and the run still completes. All
  demonstrated live and asserted by the safety suite (5/5, zero unapproved).
- Verification treats a governed tool result as provenance-verified data, not
  re-derived (Phase 8) — approval governs *whether* it runs, not the truth of an
  external system's response.
- The pause/end-stream model is robust to dropped connections (the checkpoint is
  the source of truth) but requires the client to poll `/approvals` and re-open
  the stream; a push/notification channel is Phase 11 work.
- The autonomy policy is currently per-effect and process-wide; per-tenant /
  per-domain overrides and a full approval UI are Phase 11 (Admin/Platform).
- This builds directly on Phase 8's permission descriptors and the write/
  high-impact RBAC boundary; Phase 10 (GraphRAG) and beyond inherit the gate
  unchanged — any new side-effecting executor is governed by implementing
  `preflight`.
