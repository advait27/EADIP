"""Reliability suite — fault injection against the real pipeline (Phase 12,
NFR-03/04, TAD Ch 16, AP-7).

Six scenarios, each injecting a failure into the orchestrated pipeline and
asserting the platform's reliability property survives it:

  1. checkpoint_resume    — the process "dies" mid-run (the stream is abandoned
                            after the first completed step); a NEW engine
                            instance resumes from the checkpoint and completes
                            without re-executing completed steps.
  2. idempotent_replay    — re-driving an already-DONE run executes zero steps
                            and changes nothing (safe redelivery/retries).
  3. retry_recovery       — a step that fails transiently succeeds on retry
                            (attempts recorded; no failed run).
  4. breaker_trip_recover — a flapping tool trips the circuit breaker (calls
                            refused fast, no hammering); after the cooldown a
                            half-open probe restores service.
  5. graceful_degradation — a hard-down retrieval backend still yields a
                            coherent partial brief (bounded, fail-safe), never
                            a crash.
  6. runaway_cost_stop    — a pathologically expensive step trips the cost
                            ceiling; the run stops bounded with partial results.

Deterministic and offline (heuristic agents, in-memory stores, in-process
DuckDB, injected clocks). Run via `eadip-reliability`; non-zero exit fails CI.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from uuid import uuid4

from eadip.adapters.duckdb_warehouse import DuckDBWarehouse
from eadip.adapters.memory_vector_store import InMemoryVectorStore
from eadip.analytics.factory import build_analytics_service
from eadip.config.settings import get_settings
from eadip.domain.entities import RunStatus
from eadip.mcp.breaker import CircuitBreaker
from eadip.mcp.demo_tools import DEMO_CONFIG, build_demo_transport
from eadip.mcp.registry import InMemoryServerStore, McpManager
from eadip.orchestrator.checkpoint import Checkpointer, InMemoryCheckpointer
from eadip.orchestrator.executors import Executor
from eadip.orchestrator.factory import build_orchestrator
from eadip.orchestrator.models import PlanStep, StepResult
from eadip.orchestrator.state import RunState
from eadip.retrieval.factory import build_retrieval_service
from eadip.security.identity import Identity
from eadip.security.policy import PolicyDecisionPoint
from eadip.security.rbac import default_catalog

_QUESTION = "Why did EMEA gross margin fall last quarter?"


@dataclass
class ReliabilityCase:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class ReliabilityReport:
    cases: list[ReliabilityCase] = field(default_factory=list)

    @property
    def passed(self) -> int:
        return sum(c.passed for c in self.cases)

    @property
    def total(self) -> int:
        return len(self.cases)

    @property
    def ok(self) -> bool:
        return all(c.passed for c in self.cases)


class CountingExecutor:
    """Wraps a real executor and counts executions per step id — the probe for
    exactly-once semantics across crash/resume and replay."""

    def __init__(self, inner: Executor) -> None:
        self.kind = inner.kind
        self._inner = inner
        self.calls: dict[str, int] = {}

    async def execute(self, step: PlanStep, state: RunState) -> StepResult:
        self.calls[step.id] = self.calls.get(step.id, 0) + 1
        return await self._inner.execute(step, state)


class FailOnceExecutor:
    """Fails each step's first attempt (transient fault), then delegates."""

    def __init__(self, inner: Executor) -> None:
        self.kind = inner.kind
        self._inner = inner
        self._failed_once: set[str] = set()

    async def execute(self, step: PlanStep, state: RunState) -> StepResult:
        if step.id not in self._failed_once:
            self._failed_once.add(step.id)
            raise RuntimeError("injected transient fault")
        return await self._inner.execute(step, state)


class DownExecutor:
    """A hard-down backend: every attempt fails."""

    def __init__(self, kind: str) -> None:
        self.kind = kind

    async def execute(self, step: PlanStep, state: RunState) -> StepResult:
        raise RuntimeError("injected outage")


class ExpensiveExecutor:
    """Each execution reports a runaway cost (loops the reflection cycle)."""

    def __init__(self, inner: Executor, cost_usd: float) -> None:
        self.kind = inner.kind
        self._inner = inner
        self._cost = cost_usd

    async def execute(self, step: PlanStep, state: RunState) -> StepResult:
        result = await self._inner.execute(step, state)
        return result.model_copy(update={"cost_usd": self._cost})


def _engine(checkpointer: Checkpointer, wrap=None):  # type: ignore[no-untyped-def]
    """The real offline pipeline (retrieval + analytics + verify/report), with an
    optional executor wrapper injected for fault instrumentation."""
    s = get_settings()
    orch = build_orchestrator(
        s,
        retrieval_service=build_retrieval_service(s, InMemoryVectorStore()),
        analytics_service=build_analytics_service(s, DuckDBWarehouse()),
        warehouse=DuckDBWarehouse(),
        checkpointer=checkpointer,
    )
    if wrap is not None:
        orch._executors = {k: wrap(v) for k, v in orch._executors.items()}  # noqa: SLF001
    return orch


def _state() -> RunState:
    return RunState(
        run_id=uuid4(),
        tenant_id=uuid4(),
        user_id=uuid4(),
        question=_QUESTION,
        acl_tags=("analyst",),
    )


async def _checkpoint_resume() -> ReliabilityCase:
    cp = InMemoryCheckpointer()
    counters: list[CountingExecutor] = []

    def wrap(inner: Executor) -> CountingExecutor:
        counter = CountingExecutor(inner)
        counters.append(counter)
        return counter

    crashed = _engine(cp, wrap)
    state = _state()
    await cp.save(state)

    # "Crash": abandon the stream after the first completed step — the engine
    # stops mid-run; everything up to the crash is checkpointed.
    async for event in crashed.stream(state):
        if event.type == "step.completed":
            break

    # "Restart": a NEW engine (fresh process image) resumes from the checkpoint.
    resumed_engine = _engine(cp, wrap)
    resumed = await cp.load(state.tenant_id, state.run_id)
    assert resumed is not None
    await resumed_engine.run(resumed)

    calls: dict[str, int] = {}
    for c in counters:
        for step_id, n in c.calls.items():
            calls[step_id] = calls.get(step_id, 0) + n
    exactly_once = all(n == 1 for n in calls.values())
    done = resumed.status is RunStatus.DONE and resumed.brief is not None
    ok = done and exactly_once and len(calls) >= 2
    return ReliabilityCase(
        "checkpoint_resume",
        ok,
        f"resumed to {resumed.status}; step executions {calls} (each exactly once)",
    )


async def _idempotent_replay() -> ReliabilityCase:
    cp = InMemoryCheckpointer()
    engine = _engine(cp)
    state = _state()
    await cp.save(state)
    await engine.run(state)
    findings_before = len(state.findings)

    counters: list[CountingExecutor] = []

    def wrap(inner: Executor) -> CountingExecutor:
        counter = CountingExecutor(inner)
        counters.append(counter)
        return counter

    replay_engine = _engine(cp, wrap)
    replayed = await cp.load(state.tenant_id, state.run_id)
    assert replayed is not None
    await replay_engine.run(replayed)  # redelivered "resume" of a DONE run
    re_executions = sum(sum(c.calls.values()) for c in counters)
    ok = re_executions == 0 and len(replayed.findings) == findings_before
    return ReliabilityCase(
        "idempotent_replay",
        ok,
        f"replay of a done run: {re_executions} re-executions (target 0), "
        f"findings {findings_before}->{len(replayed.findings)}",
    )


async def _retry_recovery() -> ReliabilityCase:
    cp = InMemoryCheckpointer()
    engine = _engine(cp, FailOnceExecutor)
    state = _state()
    await cp.save(state)
    await engine.run(state)
    attempts = {r.step_id: r.attempts for r in state.step_results}
    retried = [s for s, n in attempts.items() if n >= 2]
    ok = state.status is RunStatus.DONE and bool(retried) and all(r.ok for r in state.step_results)
    return ReliabilityCase(
        "retry_recovery",
        ok,
        f"status={state.status}; attempts per step {attempts} (transient faults retried)",
    )


async def _breaker_trip_recover() -> ReliabilityCase:
    clock = {"t": 0.0}
    healthy = build_demo_transport()
    outage = {"on": True}

    class FlappingTransport:
        async def discover(self):  # type: ignore[no-untyped-def]
            return await healthy.discover()

        async def invoke(self, tool, arguments):  # type: ignore[no-untyped-def]
            if outage["on"]:
                raise RuntimeError("injected tool outage")
            return await healthy.invoke(tool, arguments)

        async def ping(self) -> None:
            if outage["on"]:
                raise RuntimeError("injected tool outage")

    manager = McpManager(
        InMemoryServerStore(),
        PolicyDecisionPoint(default_catalog()),
        transport_provider=lambda config, credential: FlappingTransport(),
        breaker=CircuitBreaker(
            failure_threshold=2,
            base_cooldown_s=5.0,
            now=lambda: clock["t"],
        ),
        now=lambda: clock["t"],
    )
    identity = Identity(user_id=uuid4(), tenant_id=uuid4(), roles=("analyst",))
    await manager.register(identity.tenant_id, DEMO_CONFIG)

    args: dict[str, object] = {"currency": "EUR"}
    for _ in range(2):  # trip the breaker (threshold 2)
        await manager.invoke(identity, DEMO_CONFIG.name, "fx_rate", args)
    tripped = await manager.invoke(identity, DEMO_CONFIG.name, "fx_rate", args)
    refused_fast = tripped.reason == "circuit_open"

    outage["on"] = False
    still_open = (await manager.invoke(identity, DEMO_CONFIG.name, "fx_rate", args)).reason
    clock["t"] = 6.0  # past the cooldown -> HALF-OPEN probe allowed
    probe = await manager.invoke(identity, DEMO_CONFIG.name, "fx_rate", args)
    recovered = probe.ok
    ok = refused_fast and still_open == "circuit_open" and recovered
    return ReliabilityCase(
        "breaker_trip_recover",
        ok,
        f"open->refused fast ({refused_fast}), held during cooldown "
        f"({still_open == 'circuit_open'}), half-open probe recovered ({recovered})",
    )


async def _graceful_degradation() -> ReliabilityCase:
    cp = InMemoryCheckpointer()
    engine = _engine(cp)
    engine._executors["retrieve"] = DownExecutor("retrieve")  # noqa: SLF001 — inject outage
    state = _state()
    await cp.save(state)
    await engine.run(state)
    # Retrieval is down for the whole run; analytics still lands findings and the
    # verify+report stage still produces a coherent (partial) brief.
    partial_brief = state.brief is not None and bool(state.brief.headline)
    analytics_findings = sum(1 for f in state.findings if f.source == "analytics")
    ok = partial_brief and analytics_findings > 0
    return ReliabilityCase(
        "graceful_degradation",
        ok,
        f"retrieval hard-down: brief={partial_brief}, "
        f"analytics findings={analytics_findings} (partial answer, no crash)",
    )


async def _runaway_cost_stop() -> ReliabilityCase:
    cp = InMemoryCheckpointer()
    engine = _engine(cp, lambda inner: ExpensiveExecutor(inner, cost_usd=5.0))
    state = _state()
    await cp.save(state)
    events = [e async for e in engine.stream(state)]
    stopped = state.stop_reason == "cost_ceiling"
    bounded_event = any(e.type == "bound.stop" for e in events)
    finished = events[-1].type == "run.done"  # ends cleanly, not with a crash
    ok = stopped and bounded_event and finished
    return ReliabilityCase(
        "runaway_cost_stop",
        ok,
        f"stop_reason={state.stop_reason}, cost=${state.cost_usd:.2f} "
        f"(ceiling enforced, clean shutdown)",
    )


async def run_reliability_suite() -> ReliabilityReport:
    cases = [
        await _checkpoint_resume(),
        await _idempotent_replay(),
        await _retry_recovery(),
        await _breaker_trip_recover(),
        await _graceful_degradation(),
        await _runaway_cost_stop(),
    ]
    return ReliabilityReport(cases=cases)


async def _main() -> int:
    report = await run_reliability_suite()
    for c in report.cases:
        mark = "PASS" if c.passed else "FAIL"
        print(f"  [{mark}] {c.name}: {c.detail}")
    print(f"reliability suite: {report.passed}/{report.total} passed (fault injection)")
    return 0 if report.ok else 1


def main() -> None:
    raise SystemExit(asyncio.run(_main()))


if __name__ == "__main__":
    main()
