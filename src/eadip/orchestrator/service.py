"""Orchestration engine (TAD Ch 5/6) — the brain.

A purpose-built, deterministic state-graph driver implementing the LangGraph
pattern: interpret → plan → route → execute → reflect, with conditional edges
(the reflection/re-plan loop and safe parallel branches), durable checkpointing
(resume-on-crash), streamed progress, and hard resource bounds (iteration cap,
per-run cost ceiling, global deadline, per-step timeout, plan-similarity loop
detection). Built behind clean agent/executor/checkpointer seams rather than
taking the LangGraph dependency, so the whole engine is offline-deterministic
and unit-testable; a LangGraph assembly could slot in behind the same seams.

`stream()` mutates and checkpoints `state` as it runs and yields progress
`Event`s; `run()` drains the stream and returns the final state.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Callable
from typing import TYPE_CHECKING

from eadip.agents.interpreter import GoalInterpreter
from eadip.agents.planner import Planner
from eadip.agents.reflection import ReflectionAgent
from eadip.agents.router import Router
from eadip.approval.models import ApprovalRequest, ApprovalStatus
from eadip.approval.policy import AutonomyPolicy

if TYPE_CHECKING:
    from eadip.approval.models import ProposedAction
from eadip.domain.entities import RunStatus
from eadip.orchestrator.checkpoint import Checkpointer
from eadip.orchestrator.executors import Executor, PreflightExecutor
from eadip.orchestrator.models import Event, Plan, PlanStep, StepResult
from eadip.orchestrator.resilience import detect_loop, with_retry, with_timeout
from eadip.orchestrator.state import RunState
from eadip.verification.service import VerificationReportingService

if TYPE_CHECKING:
    from eadip.memory.agent import MemoryAgent


class OrchestratorService:
    def __init__(
        self,
        *,
        interpreter: GoalInterpreter,
        planner: Planner,
        router: Router,
        reflection: ReflectionAgent,
        executors: dict[str, Executor],
        checkpointer: Checkpointer,
        max_iterations: int = 6,
        max_cost_usd: float = 2.50,
        deadline_s: float = 480.0,
        step_timeout_s: float = 60.0,
        retry_attempts: int = 2,
        retry_base_delay_s: float = 0.05,
        loop_similarity: float = 0.92,
        max_parallelism: int = 4,
        reporter: VerificationReportingService | None = None,
        autonomy: AutonomyPolicy | None = None,
        memory: MemoryAgent | None = None,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._interpreter = interpreter
        self._planner = planner
        self._router = router
        self._reflection = reflection
        self._executors = executors
        self._cp = checkpointer
        self._reporter = reporter
        self._autonomy = autonomy or AutonomyPolicy.safe_default()
        self._memory = memory
        self._max_iterations = max_iterations
        self._max_cost_usd = max_cost_usd
        self._deadline_s = deadline_s
        self._step_timeout_s = step_timeout_s
        self._retry_attempts = retry_attempts
        self._retry_base_delay_s = retry_base_delay_s
        self._loop_similarity = loop_similarity
        self._max_parallelism = max_parallelism
        self._now = now

    async def run(self, state: RunState) -> RunState:
        async for _ in self.stream(state):
            pass
        return state

    async def stream(self, state: RunState) -> AsyncIterator[Event]:  # noqa: C901 - state machine
        start = self._now()

        def elapsed() -> float:
            return self._now() - start

        yield Event(
            type="run.accepted", data={"run_id": str(state.run_id), "question": state.question}
        )

        # INTERPRET (skipped when resuming a checkpoint that already has a goal).
        if state.goal is None:
            state.status = RunStatus.PLANNING
            state.goal = await self._interpreter.interpret(
                state.question, tenant_id=state.tenant_id
            )
            await self._cp.save(state)
            yield Event(type="goal.interpreted", data=state.goal.model_dump())

        assert state.goal is not None
        while True:
            breach = self._bound_breached(state, elapsed())
            if breach:
                yield self._stop(state, breach)
                break

            # (RE)PLAN — first pass, or because the last reflection asked for it.
            last = state.reflections[-1] if state.reflections else None
            if state.plan is None or (last is not None and last.should_replan):
                gaps = last.gaps if last else []
                reused = await self._recall_plan(state) if state.plan is None else None
                new_plan = reused or await self._planner.plan(state.goal, gaps)
                signature = new_plan.signature()
                if detect_loop(state.plan_history, signature, self._loop_similarity):
                    yield self._stop(state, "loop_detected")
                    break
                if state.iterations >= self._max_iterations:
                    yield self._stop(state, "iteration_cap")
                    break
                state.iterations += 1
                state.plan = new_plan
                state.plan_history.append(signature)
                state.status = RunStatus.PLANNING
                await self._cp.save(state)
                yield Event(
                    type="plan.reused" if reused is not None else "plan.created",
                    data={
                        "iteration": state.iterations,
                        "rationale": new_plan.rationale,
                        "reused": reused is not None,  # from episodic memory (FR-045)
                        # Which backend planned it — a trace must show if the AI ran.
                        "produced_by": new_plan.produced_by,
                        "fallback_reason": new_plan.fallback_reason,
                        "steps": [
                            {"id": s.id, "kind": s.kind, "description": s.description}
                            for s in new_plan.steps
                        ],
                    },
                )

            # ROUTE + EXECUTE pending steps in safe parallel batches.
            state.status = RunStatus.EXECUTING
            assert state.plan is not None
            while state.pending_steps():
                breach = self._bound_breached(state, elapsed())
                if breach:
                    yield self._stop(state, breach)
                    break
                batch = self._router.next_batch(
                    state.plan, set(state.completed_step_ids), self._max_parallelism
                )
                if not batch:  # remaining steps blocked on unmet deps
                    break

                # GOVERNED AUTONOMY (Phase 9, interrupt_before): consult the
                # autonomy policy for each step in the batch. Rejected steps are
                # skipped + recorded; a step needing approval is held back (a
                # pending request raised). Independent runnable steps in the same
                # batch still execute — the gate holds only the gated step; once no
                # runnable work remains, the run pauses for the human.
                runnable: list[PlanStep] = []
                skipped: list[StepResult] = []
                pending_requests: list[ApprovalRequest] = []
                for step in batch:
                    if step.id in state.rejected_step_ids:
                        skipped.append(self._skipped_result(step))
                        continue
                    action = await self._preflight(step, state)
                    if action is not None and self._autonomy.requires_approval(action):
                        approval = state.approval_for_step(step.id)
                        if approval is None or approval.status is ApprovalStatus.PENDING:
                            pending_requests.append(self._raise_approval(state, action, approval))
                            continue  # hold this step back; keep gathering runnable work
                        # APPROVED: run the approved (possibly edited) payload,
                        # under the approver's authority (user-scoped token) — the
                        # requester may not hold the write grant themselves.
                        step.params["arguments"] = dict(approval.approved_payload)
                        if approval.approver_roles:
                            step.params["_approver_roles"] = list(approval.approver_roles)
                    runnable.append(step)

                if skipped:
                    state.apply_step_results(skipped)
                    await self._cp.save(state)
                    for r in skipped:
                        yield Event(
                            type="step.skipped",
                            data={"id": r.step_id, "summary": r.summary},
                        )

                # Nothing runnable this round and an approval is pending -> pause.
                if not runnable and pending_requests:
                    state.status = RunStatus.AWAITING_APPROVAL
                    await self._cp.save(state)
                    for request in pending_requests:
                        yield Event(
                            type="approval.required",
                            data={
                                "approval_id": str(request.id),
                                "action": request.action.model_dump(mode="json"),
                            },
                        )
                    return  # end the stream; a decision + re-open resumes the run

                if not runnable:
                    break  # only blocked/skipped steps remain -> leave the execute loop

                for step in runnable:
                    yield Event(
                        type="step.started",
                        data={"id": step.id, "kind": step.kind, "description": step.description},
                    )
                results = await asyncio.gather(*(self._run_step(s, state) for s in runnable))
                state.apply_step_results(list(results))
                await self._cp.save(state)
                for r in results:
                    yield Event(
                        type="step.completed",
                        data={
                            "id": r.step_id,
                            "ok": r.ok,
                            "summary": r.summary,
                            "cost_usd": r.cost_usd,
                            "attempts": r.attempts,
                            "findings": len(r.findings),
                        },
                    )
                    for f in r.findings:
                        yield Event(type="finding.partial", data=f.model_dump())
            if state.stop_reason:
                break

            # REFLECT — sufficient? otherwise loop back to re-plan.
            reflection = await self._reflection.reflect(
                state.goal, state.findings, state.step_results
            )
            state.reflections.append(reflection)
            await self._cp.save(state)
            yield Event(
                type="reflection",
                data={
                    "sufficient": reflection.sufficient,
                    "gaps": reflection.gaps,
                    "produced_by": reflection.produced_by,
                    "fallback_reason": reflection.fallback_reason,
                },
            )
            if reflection.sufficient or not reflection.should_replan:
                break
            yield Event(
                type="replan", data={"iteration": state.iterations, "gaps": reflection.gaps}
            )

        # VERIFY + REPORT (Phase 7) — independently re-derive every number and
        # build the executive brief. The reporter runs from a fresh context.
        if self._reporter is not None and state.findings:
            state.status = RunStatus.VERIFYING
            objective = state.goal.objective if state.goal else state.question
            report, brief = await self._reporter.produce(
                state.findings,
                tenant_id=state.tenant_id,
                question=state.question,
                objective=objective,
                stop_reason=state.stop_reason,
            )
            state.verified_claims = report.claims
            state.snapshot_commitments = dict(report.snapshot_commitments)
            state.brief = brief
            await self._cp.save(state)
            for c in report.claims:
                yield Event(
                    type="claim.verified",
                    data={
                        "claim": c.claim,
                        "status": str(c.status),
                        "confidence": c.confidence,
                        "method": c.method,
                    },
                )
            yield Event(
                type="verification.summary",
                data={
                    "verified": report.verified,
                    "unverified": report.unverified,
                    "conflicting": report.conflicting,
                    "snapshot_commitments": dict(report.snapshot_commitments),
                },
            )
            for rec in brief.recommendations:
                yield Event(type="recommendation", data=rec.model_dump())
            yield Event(type="brief", data=brief.model_dump(mode="json"))

        # FINALIZE.
        state.elapsed_s = elapsed()
        state.status = RunStatus.DONE if state.findings else RunStatus.FAILED
        await self._cp.save(state)

        # CONSOLIDATE (Phase 11, FR-045): distil a finished run into long-term
        # memory so the next similar question reuses this plan + shared facts.
        if self._memory is not None and state.status is RunStatus.DONE:
            consolidation = await self._memory.consolidate(state)
            yield Event(type="memory.consolidated", data=consolidation.model_dump())

        yield Event(
            type="run.done",
            data={
                "status": str(state.status),
                "findings": len(state.findings),
                "cost_usd": round(state.cost_usd, 6),
                "iterations": state.iterations,
                "elapsed_s": round(state.elapsed_s, 3),
                "stop_reason": state.stop_reason,
            },
        )

    # --- helpers --------------------------------------------------------------
    def _bound_breached(self, state: RunState, elapsed: float) -> str | None:
        if state.cost_usd > self._max_cost_usd:
            return "cost_ceiling"
        if elapsed > self._deadline_s:
            return "deadline"
        return None

    def _stop(self, state: RunState, reason: str) -> Event:
        state.stop_reason = reason
        return Event(type="bound.stop", data={"reason": reason})

    # --- memory: episodic plan reuse (Phase 11) ------------------------------
    async def _recall_plan(self, state: RunState) -> Plan | None:
        """Reuse a prior plan for a repeat question (episodic memory). The replayed
        plan is still routed, executed and verified normally — reuse skips only
        the (re)planning step, not any governance."""
        if self._memory is None:
            return None
        episode = await self._memory.recall_plan(state.tenant_id, state.question)
        if episode is None or not episode.plan_steps:
            return None
        steps = [PlanStep.model_validate(s) for s in episode.plan_steps]
        return Plan(
            steps=steps,
            rationale=f"reused prior plan (episode hits={episode.hits})",
            produced_by="memory",
        )

    # --- governed autonomy (Phase 9) -----------------------------------------
    async def _preflight(self, step: PlanStep, state: RunState) -> ProposedAction | None:
        """Ask the step's executor what side-effecting action it would take. Only
        preflight-capable executors (e.g. the tool executor) return an action."""
        executor = self._executors.get(step.kind)
        if isinstance(executor, PreflightExecutor):
            return await executor.preflight(step, state)
        return None

    def _raise_approval(
        self, state: RunState, action: ProposedAction, existing: ApprovalRequest | None
    ) -> ApprovalRequest:
        """Record (or return the already-open) pending approval for a gated step."""
        if existing is not None and existing.status is ApprovalStatus.PENDING:
            return existing
        request = ApprovalRequest(action=action, requested_at_s=self._now())
        state.approvals.append(request)
        return request

    @staticmethod
    def _skipped_result(step: PlanStep) -> StepResult:
        return StepResult(
            step_id=step.id,
            kind=step.kind,
            ok=True,  # skipping a rejected step is a governed, non-failing outcome
            summary="skipped: rejected by approver",
        )

    async def _run_step(self, step: PlanStep, state: RunState) -> StepResult:
        executor = self._executors.get(step.kind)
        if executor is None:
            return StepResult(
                step_id=step.id, kind=step.kind, ok=False, error=f"no executor for '{step.kind}'"
            )
        attempts = {"n": 1}

        def on_retry(n: int, _exc: Exception) -> None:
            attempts["n"] = n + 1

        async def attempt() -> StepResult:
            return await with_timeout(executor.execute(step, state), self._step_timeout_s)

        try:
            result = await with_retry(
                attempt,
                attempts=self._retry_attempts,
                base_delay=self._retry_base_delay_s,
                on_retry=on_retry,
            )
        except Exception as exc:  # noqa: BLE001 — exhausted retries -> failed step
            return StepResult(
                step_id=step.id,
                kind=step.kind,
                ok=False,
                error=str(exc)[:200] or type(exc).__name__,
                attempts=self._retry_attempts + 1,
            )
        return result.model_copy(update={"attempts": attempts["n"]})
