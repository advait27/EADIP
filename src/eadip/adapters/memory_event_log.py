"""Process-local event log (Glass Box): the dev/test default.

Replay-then-tail is gap-free: a subscriber queue is registered *before* the
replay snapshot is taken (no ``await`` in between, so under asyncio nothing can
interleave), and anything the queue receives with ``seq`` <= the last replayed
event is dropped as a duplicate.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncGenerator, Callable
from uuid import UUID

from eadip.orchestrator.models import Event
from eadip.ports.events import LoggedEvent, is_terminal


class InMemoryEventLog:
    def __init__(self, now: Callable[[], float] = time.time, *, max_runs: int = 500) -> None:
        # Insertion-ordered so eviction walks oldest-first.
        self._events: dict[UUID, list[LoggedEvent]] = {}
        self._subs: dict[UUID, set[asyncio.Queue[LoggedEvent]]] = {}
        self._now = now
        self._max_runs = max(1, max_runs)

    async def append(self, run_id: UUID, event: Event) -> LoggedEvent:
        events = self._events.get(run_id)
        if events is None:
            events = self._events[run_id] = []
            self._evict()
        logged = LoggedEvent.from_event(len(events) + 1, self._now(), event)
        events.append(logged)
        for queue in self._subs.get(run_id, ()):
            queue.put_nowait(logged)
        return logged

    def _evict(self) -> None:
        """Bound memory: drop the oldest *terminal* runs (never one being
        streamed or still running) until at most ``max_runs`` remain."""
        if len(self._events) <= self._max_runs:
            return
        for rid in list(self._events):
            if len(self._events) <= self._max_runs:
                break
            events = self._events[rid]
            if rid in self._subs or not events or not is_terminal(events[-1].type):
                continue
            del self._events[rid]

    def run_count(self) -> int:
        return len(self._events)

    async def read(self, run_id: UUID, after_seq: int = 0) -> list[LoggedEvent]:
        return [e for e in self._events.get(run_id, []) if e.seq > after_seq]

    async def last_seq(self, run_id: UUID) -> int:
        events = self._events.get(run_id)
        return events[-1].seq if events else 0

    async def stream(self, run_id: UUID, after_seq: int = 0) -> AsyncGenerator[LoggedEvent]:
        queue: asyncio.Queue[LoggedEvent] = asyncio.Queue()
        self._subs.setdefault(run_id, set()).add(queue)
        try:
            # A cursor ahead of the log (stale/foreign Last-Event-ID) is clamped to
            # the head: replay nothing, but never drop the live events that follow.
            cursor = min(after_seq, await self.last_seq(run_id))
            for logged in await self.read(run_id, cursor):
                cursor = logged.seq
                yield logged
            while True:
                logged = await queue.get()
                if logged.seq <= cursor:
                    continue  # already replayed
                cursor = logged.seq
                yield logged
        finally:
            subs = self._subs.get(run_id)
            if subs is not None:
                subs.discard(queue)
                if not subs:
                    del self._subs[run_id]

    def subscriber_count(self, run_id: UUID) -> int:
        return len(self._subs.get(run_id, ()))
