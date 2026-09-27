"""Run event log port (Glass Box).

The orchestrator yields a stream of :class:`Event`s while a run executes. The
event log makes that stream durable and addressable: every event gets a
monotonic per-run ``seq`` and a timestamp, so a client can (re)connect at any
point (``Last-Event-ID``), a replay can be reconstructed with its original
timing, and the run's progress no longer depends on any single HTTP connection
staying open. In-memory by default; a Postgres adapter is the production path.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any, Protocol
from uuid import UUID

from pydantic import BaseModel, Field

from eadip.orchestrator.models import Event

# Event types after which a run's log receives nothing more until it is resumed
# (approval) or ever (done/failed). Subscribers stop tailing on these.
TERMINAL_EVENTS: frozenset[str] = frozenset({"run.done", "run.failed", "approval.required"})


def is_terminal(event_type: str) -> bool:
    return event_type in TERMINAL_EVENTS


class LoggedEvent(BaseModel):
    """An :class:`Event` as recorded: ordered (``seq``) and timestamped (``at``,
    UNIX seconds). Serialises straight over SSE (``id``/``event``/``data``)."""

    seq: int
    at: float
    type: str
    data: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_event(cls, seq: int, at: float, event: Event) -> LoggedEvent:
        return cls(seq=seq, at=at, type=event.type, data=event.data)


class EventLog(Protocol):
    async def append(self, run_id: UUID, event: Event) -> LoggedEvent: ...

    async def read(self, run_id: UUID, after_seq: int = 0) -> list[LoggedEvent]: ...

    async def last_seq(self, run_id: UUID) -> int: ...

    def stream(self, run_id: UUID, after_seq: int = 0) -> AsyncGenerator[LoggedEvent]:
        """Replay everything after ``after_seq``, then tail live events. The
        iterator never ends on its own — the consumer stops on a terminal event."""
        ...
