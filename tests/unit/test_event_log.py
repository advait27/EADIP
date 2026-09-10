"""Event log (Glass Box): durable, ordered run events with replay + live tail."""

from __future__ import annotations

import asyncio
from contextlib import aclosing
from uuid import uuid4

import pytest

from eadip.adapters.memory_event_log import InMemoryEventLog
from eadip.orchestrator.models import Event


async def test_append_assigns_monotonic_seq() -> None:
    log = InMemoryEventLog()
    run_id = uuid4()
    a = await log.append(run_id, Event(type="run.accepted", data={"x": 1}))
    b = await log.append(run_id, Event(type="plan.created", data={}))
    assert (a.seq, b.seq) == (1, 2)
    assert a.type == "run.accepted" and a.data == {"x": 1}
    assert a.at <= b.at
    assert await log.last_seq(run_id) == 2
    assert await log.last_seq(uuid4()) == 0


async def test_read_replays_after_seq() -> None:
    log = InMemoryEventLog()
    run_id = uuid4()
    for i in range(5):
        await log.append(run_id, Event(type=f"e{i}"))
    assert [e.seq for e in await log.read(run_id)] == [1, 2, 3, 4, 5]
    assert [e.type for e in await log.read(run_id, after_seq=3)] == ["e3", "e4"]
    assert await log.read(uuid4()) == []


async def test_runs_are_isolated() -> None:
    log = InMemoryEventLog()
    a, b = uuid4(), uuid4()
    await log.append(a, Event(type="a1"))
    await log.append(b, Event(type="b1"))
    assert [e.type for e in await log.read(a)] == ["a1"]
    assert [e.type for e in await log.read(b)] == ["b1"]


async def test_stream_replays_then_tails_without_gap_or_duplicate() -> None:
    log = InMemoryEventLog()
    run_id = uuid4()
    await log.append(run_id, Event(type="e1"))
    await log.append(run_id, Event(type="e2"))

    seen: list[str] = []

    async def consume() -> None:
        async for ev in log.stream(run_id, after_seq=1):
            seen.append(ev.type)
            if ev.type == "e4":
                break

    task = asyncio.create_task(consume())
    await asyncio.sleep(0)  # let the consumer subscribe + replay
    await log.append(run_id, Event(type="e3"))
    await log.append(run_id, Event(type="e4"))
    await asyncio.wait_for(task, timeout=2)
    assert seen == ["e2", "e3", "e4"]


async def test_stream_delivers_events_appended_between_subscribe_and_replay() -> None:
    """An event appended while a subscriber is mid-replay must arrive exactly once."""
    log = InMemoryEventLog()
    run_id = uuid4()
    await log.append(run_id, Event(type="e1"))
    seen: list[int] = []

    async def consume() -> None:
        async for ev in log.stream(run_id):
            seen.append(ev.seq)
            if ev.seq == 3:
                break

    task = asyncio.create_task(consume())
    await log.append(run_id, Event(type="e2"))
    await log.append(run_id, Event(type="e3"))
    await asyncio.wait_for(task, timeout=2)
    assert seen == [1, 2, 3]


async def test_subscriber_removed_after_stream_ends() -> None:
    log = InMemoryEventLog()
    run_id = uuid4()

    await log.append(run_id, Event(type="e1"))
    async with aclosing(log.stream(run_id)) as events:
        async for _ in events:
            assert log.subscriber_count(run_id) == 1
            break
    assert log.subscriber_count(run_id) == 0


@pytest.mark.parametrize("after_seq", [0, 1, 99])
async def test_stream_on_empty_or_ahead_cursor_only_tails(after_seq: int) -> None:
    """A Last-Event-ID ahead of the log is clamped: nothing replays, live events flow."""
    log = InMemoryEventLog()
    run_id = uuid4()

    async def consume() -> list[int]:
        out: list[int] = []
        async for ev in log.stream(run_id, after_seq=after_seq):
            out.append(ev.seq)
            break
        return out

    task = asyncio.create_task(consume())
    await asyncio.sleep(0)
    await log.append(run_id, Event(type="live"))
    assert await asyncio.wait_for(task, timeout=2) == [1]


async def test_eviction_drops_oldest_terminal_runs_only() -> None:
    log = InMemoryEventLog(max_runs=2)
    live, old_done, new_done = uuid4(), uuid4(), uuid4()
    await log.append(live, Event(type="run.accepted"))  # still running: never evicted
    await log.append(old_done, Event(type="run.done"))
    await log.append(new_done, Event(type="run.done"))
    assert log.run_count() == 2
    assert await log.read(old_done) == []  # oldest terminal run evicted
    assert [e.type for e in await log.read(live)] == ["run.accepted"]
    assert [e.type for e in await log.read(new_done)] == ["run.done"]
