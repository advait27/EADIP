"""RunExecutor (Glass Box): runs progress in the background, the log is always
sealed with a terminal event, hooks fire, the gate is released."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

from eadip.adapters.memory_event_log import InMemoryEventLog
from eadip.domain.entities import RunStatus
from eadip.orchestrator.checkpoint import InMemoryCheckpointer
from eadip.orchestrator.executor import RunExecutor, StartResult, is_terminal
from eadip.orchestrator.models import Event
from eadip.orchestrator.state import RunState
from eadip.platform.ratelimit import ConcurrencyGate
from eadip.ports.events import LoggedEvent


class ScriptedOrchestrator:
    """Yields a scripted event list; optionally raises mid-stream."""

    def __init__(self, events: list[Event], *, raise_after: int | None = None) -> None:
        self._events = events
        self._raise_after = raise_after
        self.calls = 0

    async def stream(self, state: RunState) -> AsyncIterator[Event]:
        self.calls += 1
        for i, ev in enumerate(self._events):
            if self._raise_after is not None and i == self._raise_after:
                raise RuntimeError("engine exploded")
            await asyncio.sleep(0)
            if ev.type == "run.done":
                state.status = RunStatus.DONE
            elif ev.type == "approval.required":
                state.status = RunStatus.AWAITING_APPROVAL
            yield ev


def _state() -> RunState:
    return RunState(run_id=uuid4(), tenant_id=uuid4(), user_id=uuid4(), question="q")


def _executor(orch: Any, **kw: Any) -> tuple[RunExecutor, InMemoryEventLog, InMemoryCheckpointer]:
    log = InMemoryEventLog()
    cp = InMemoryCheckpointer()
    return RunExecutor(orchestrator=orch, checkpointer=cp, event_log=log, **kw), log, cp


async def test_start_runs_in_background_and_logs_every_event() -> None:
    orch = ScriptedOrchestrator(
        [Event(type="run.accepted"), Event(type="plan.created"), Event(type="run.done")]
    )
    ex, log, _ = _executor(orch)
    state = _state()
    assert ex.start(state) is StartResult.STARTED
    assert ex.is_running(state.run_id)
    await ex.wait(state.run_id)
    assert not ex.is_running(state.run_id)
    assert [e.type for e in await log.read(state.run_id)] == [
        "run.accepted",
        "plan.created",
        "run.done",
    ]


async def test_double_start_is_a_noop() -> None:
    orch = ScriptedOrchestrator([Event(type="run.accepted"), Event(type="run.done")])
    ex, _, _ = _executor(orch)
    state = _state()
    assert ex.start(state) is StartResult.STARTED
    assert ex.start(state) is StartResult.ALREADY_RUNNING
    await ex.wait(state.run_id)
    assert orch.calls == 1


async def test_hooks_fire_per_event_and_a_failing_hook_does_not_kill_the_run() -> None:
    orch = ScriptedOrchestrator([Event(type="run.accepted"), Event(type="run.done")])
    seen: list[str] = []

    async def good(state: RunState, ev: LoggedEvent) -> None:
        seen.append(ev.type)

    async def bad(state: RunState, ev: LoggedEvent) -> None:
        raise ValueError("boom")

    ex, log, _ = _executor(orch, hooks=[bad, good])
    state = _state()
    ex.start(state)
    await ex.wait(state.run_id)
    assert seen == ["run.accepted", "run.done"]
    assert [e.type for e in await log.read(state.run_id)][-1] == "run.done"


async def test_engine_exception_seals_the_log_with_run_failed_and_marks_checkpoint() -> None:
    orch = ScriptedOrchestrator(
        [Event(type="run.accepted"), Event(type="plan.created"), Event(type="run.done")],
        raise_after=1,
    )
    ex, log, cp = _executor(orch)
    state = _state()
    ex.start(state)
    await ex.wait(state.run_id)
    events = await log.read(state.run_id)
    assert [e.type for e in events] == ["run.accepted", "run.failed"]
    assert "engine exploded" in events[-1].data["error"]
    saved = await cp.load(state.tenant_id, state.run_id)
    assert saved is not None and saved.status is RunStatus.FAILED
    assert saved.errors and "engine exploded" in saved.errors[0]


async def test_stream_without_terminal_event_is_sealed() -> None:
    orch = ScriptedOrchestrator([Event(type="run.accepted"), Event(type="step.started")])
    ex, log, _ = _executor(orch)
    state = _state()
    ex.start(state)
    await ex.wait(state.run_id)
    types = [e.type for e in await log.read(state.run_id)]
    assert types == ["run.accepted", "step.started", "run.failed"]
    assert is_terminal(types[-1])


async def test_gate_is_acquired_on_start_and_released_after() -> None:
    gate = ConcurrencyGate(1)
    orch = ScriptedOrchestrator([Event(type="run.accepted"), Event(type="run.done")])
    ex, _, _ = _executor(orch, gate=gate)
    tenant = uuid4()
    a = RunState(run_id=uuid4(), tenant_id=tenant, user_id=uuid4(), question="a")
    b = RunState(run_id=uuid4(), tenant_id=tenant, user_id=uuid4(), question="b")
    assert ex.start(a) is StartResult.STARTED
    assert ex.start(b) is StartResult.REFUSED_CAPACITY
    await ex.wait(a.run_id)
    assert ex.start(b) is StartResult.STARTED
    await ex.wait(b.run_id)


async def test_gate_released_even_when_engine_raises() -> None:
    gate = ConcurrencyGate(1)
    orch = ScriptedOrchestrator([Event(type="run.accepted")], raise_after=0)
    ex, _, _ = _executor(orch, gate=gate)
    state = _state()
    ex.start(state)
    await ex.wait(state.run_id)
    assert gate.try_acquire(str(state.tenant_id))


async def test_resume_loads_checkpoint_and_restarts_only_resumable_runs() -> None:
    orch = ScriptedOrchestrator([Event(type="run.accepted"), Event(type="run.done")])
    ex, _, cp = _executor(orch)
    state = _state()
    assert await ex.resume(state.tenant_id, state.run_id) is None  # unknown run

    # A paused (awaiting approval) checkpoint with a goal is resumable.
    from eadip.orchestrator.models import Goal

    state.goal = Goal(objective="q")
    state.status = RunStatus.AWAITING_APPROVAL
    await cp.save(state)
    assert await ex.resume(state.tenant_id, state.run_id) is StartResult.STARTED
    await ex.wait(state.run_id)

    # A finished run is not restarted.
    done = await cp.load(state.tenant_id, state.run_id)
    assert done is not None
    done.status = RunStatus.DONE
    await cp.save(done)
    assert await ex.resume(state.tenant_id, state.run_id) is None
    # Tenant mismatch is invisible (RLS-style).
    assert await ex.resume(uuid4(), state.run_id) is None


async def test_subscriber_tails_a_live_run_to_the_terminal_event() -> None:
    orch = ScriptedOrchestrator(
        [Event(type="run.accepted"), Event(type="step.started"), Event(type="run.done")]
    )
    ex, log, _ = _executor(orch)
    state = _state()
    ex.start(state)
    seen: list[str] = []
    async for ev in log.stream(state.run_id):
        seen.append(ev.type)
        if is_terminal(ev.type):
            break
    assert seen == ["run.accepted", "step.started", "run.done"]


async def test_shutdown_cancels_running_tasks() -> None:
    class Slow:
        async def stream(self, state: RunState) -> AsyncIterator[Event]:
            yield Event(type="run.accepted")
            await asyncio.sleep(10)
            yield Event(type="run.done")

    ex, log, _ = _executor(Slow())
    state = _state()
    ex.start(state)
    await asyncio.sleep(0.01)
    await ex.shutdown()
    assert not ex.is_running(state.run_id)
    assert ex.running_count() == 0


async def test_is_running_is_false_once_a_terminal_event_is_recorded() -> None:
    """Hooks may still be executing after run.done; subscribers must not wait."""
    gate_seen: list[bool] = []

    async def slow_hook(state: RunState, ev: LoggedEvent) -> None:
        if ev.type == "run.done":
            gate_seen.append(ex.is_running(state.run_id))
            await asyncio.sleep(0.05)

    orch = ScriptedOrchestrator([Event(type="run.accepted"), Event(type="run.done")])
    ex, _, _ = _executor(orch, hooks=[slow_hook])
    state = _state()
    ex.start(state)
    await ex.wait(state.run_id)
    assert gate_seen == [False]
