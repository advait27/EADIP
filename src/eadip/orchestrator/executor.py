"""Run executor (Glass Box): a run makes progress on its own.

Before this module the orchestrator only advanced while an SSE client held
``GET /v1/runs/{id}/events`` open — the stream *was* the execution. The executor
runs the engine as a background task the moment a run is accepted (or resumed
after an approval), records every event in the :class:`EventLog`, and fires the
post-event hooks (notifications, budget charging). Streams become subscribers:
they replay and tail the log, so a client may disconnect, refresh, or share the
run without stalling it.

Guarantees:
- one task per run id (``start`` on a running run is a no-op);
- the per-tenant concurrency gate is acquired at start and released in
  ``finally``, whatever happens;
- the log always ends in a terminal event (``run.done``, ``approval.required``,
  or a synthetic ``run.failed`` when the engine raised or ended abruptly), so a
  tailing subscriber never hangs.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from enum import StrEnum
from uuid import UUID

from eadip.domain.entities import RunStatus
from eadip.observability.logging import get_logger
from eadip.orchestrator.checkpoint import Checkpointer
from eadip.orchestrator.models import Event
from eadip.orchestrator.service import OrchestratorService
from eadip.orchestrator.state import RunState
from eadip.platform.ratelimit import ConcurrencyGate
from eadip.ports.events import TERMINAL_EVENTS, EventLog, LoggedEvent, is_terminal

log = get_logger(__name__)

__all__ = ["TERMINAL_EVENTS", "RunExecutor", "StartResult", "is_terminal"]

EventHook = Callable[[RunState, LoggedEvent], Awaitable[None]]


class StartResult(StrEnum):
    STARTED = "started"
    ALREADY_RUNNING = "already_running"
    REFUSED_CAPACITY = "refused_capacity"


class RunExecutor:
    def __init__(
        self,
        *,
        orchestrator: OrchestratorService,
        checkpointer: Checkpointer,
        event_log: EventLog,
        gate: ConcurrencyGate | None = None,
        hooks: list[EventHook] | None = None,
    ) -> None:
        self._orchestrator = orchestrator
        self._cp = checkpointer
        self._log = event_log
        self._gate = gate
        self._hooks: list[EventHook] = list(hooks or [])
        self._tasks: dict[UUID, asyncio.Task[None]] = {}
        # Last event type recorded per running task: once it is terminal the run
        # is "not running" for subscribers even while hooks/finally still execute.
        self._last_type: dict[UUID, str] = {}

    # --- control ---------------------------------------------------------------
    def add_hook(self, hook: EventHook) -> None:
        self._hooks.append(hook)

    def is_running(self, run_id: UUID) -> bool:
        """True while the run's task is alive AND has not yet recorded a terminal
        event (a subscriber that sees no task will replay the log and close)."""
        task = self._tasks.get(run_id)
        if task is None or task.done():
            return False
        return not is_terminal(self._last_type.get(run_id, ""))

    def status(self, run_id: UUID) -> dict[str, object]:
        """Introspection for logs and the run status endpoint."""
        task = self._tasks.get(run_id)
        return {
            "tracked": task is not None,
            "done": task.done() if task is not None else None,
            "cancelled": task.cancelled() if task is not None and task.done() else None,
            "exception": (
                repr(task.exception())
                if task is not None and task.done() and not task.cancelled()
                else None
            ),
            "last_type": self._last_type.get(run_id),
            "tracked_runs": len(self._tasks),
        }

    def running_count(self) -> int:
        return sum(1 for t in self._tasks.values() if not t.done())

    def start(self, state: RunState) -> StartResult:
        """Begin (or continue) executing ``state`` in the background."""
        if self.is_running(state.run_id):
            return StartResult.ALREADY_RUNNING
        gate_key = str(state.tenant_id)
        if self._gate is not None and not self._gate.try_acquire(gate_key):
            return StartResult.REFUSED_CAPACITY
        self._last_type[state.run_id] = ""
        task = asyncio.create_task(self._run(state, gate_key), name=f"run:{state.run_id}")
        self._tasks[state.run_id] = task
        return StartResult.STARTED

    async def resume(self, tenant_id: UUID, run_id: UUID) -> StartResult | None:
        """Continue a checkpointed run (after an approval decision). ``None`` when
        there is no such run for the tenant; a finished run is not restarted."""
        state = await self._cp.load(tenant_id, run_id)
        if state is None:
            return None
        if not state.is_resumable():
            return StartResult.ALREADY_RUNNING if self.is_running(run_id) else None
        return self.start(state)

    async def wait(self, run_id: UUID) -> None:
        task = self._tasks.get(run_id)
        if task is not None:
            await asyncio.shield(task)

    async def shutdown(self) -> None:
        for task in list(self._tasks.values()):
            if not task.done():
                task.cancel()
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        self._tasks.clear()

    # --- the task --------------------------------------------------------------
    async def _run(self, state: RunState, gate_key: str) -> None:
        run_id = state.run_id
        last_type = ""
        try:
            async for event in self._orchestrator.stream(state):
                last_type = event.type
                await self._record(state, event)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — engine fault -> failed run, never a hang
            log.error("run.executor_failed", run_id=str(run_id), error=str(exc))
            state.status = RunStatus.FAILED
            state.errors.append(f"executor: {str(exc)[:200] or type(exc).__name__}")
            await self._cp.save(state)
            last_type = "run.failed"
            await self._record(
                state, Event(type="run.failed", data={"error": str(exc)[:200]}), safe=True
            )
        finally:
            if not is_terminal(last_type):
                # The engine ended without a terminal event: seal the log so a
                # tailing subscriber terminates instead of waiting forever.
                await self._record(
                    state,
                    Event(
                        type="run.failed", data={"error": "stream ended without a terminal event"}
                    ),
                    safe=True,
                )
            if self._gate is not None:
                self._gate.release(gate_key)
            # Only clear bookkeeping this task owns: once a terminal event is
            # recorded the run counts as "not running", so a resume() can have
            # already started a NEW task for the same run id before this
            # finally runs. Popping unconditionally would untrack that task.
            if self._tasks.get(run_id) is asyncio.current_task():
                self._tasks.pop(run_id, None)
                self._last_type.pop(run_id, None)

    async def _record(self, state: RunState, event: Event, *, safe: bool = False) -> None:
        try:
            logged = await self._log.append(state.run_id, event)
        except Exception:
            if safe:
                log.exception("run.event_log_failed", run_id=str(state.run_id))
                return
            raise
        self._last_type[state.run_id] = event.type
        for hook in self._hooks:
            try:
                await hook(state, logged)
            except Exception as exc:  # noqa: BLE001 — a hook must never kill the run
                log.warning("run.hook_failed", run_id=str(state.run_id), error=str(exc))
