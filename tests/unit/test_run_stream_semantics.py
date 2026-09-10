"""Glass Box stream semantics: runs execute in the background; the SSE stream is
a subscriber that replays from Last-Event-ID and closes on a terminal event."""

from __future__ import annotations

import importlib.util
import re

import pytest
from fastapi.testclient import TestClient

from eadip.gateway.dependencies import get_concurrency_gate, get_run_executor

_DUCKDB = importlib.util.find_spec("duckdb") is not None
_Q = "Why did EMEA gross margin fall last quarter?"


def _messages(body: str) -> list[tuple[int, str]]:
    """(id, event) pairs in stream order."""
    out: list[tuple[int, str]] = []
    for block in body.replace("\r\n", "\n").split("\n\n"):
        mid = re.search(r"^id: (\d+)$", block, re.M)
        mev = re.search(r"^event: (.+)$", block, re.M)
        if mid and mev:
            out.append((int(mid.group(1)), mev.group(1).strip()))
    return out


def _stream(client: TestClient, run_id: str, **headers: str) -> list[tuple[int, str]]:
    with client.stream("GET", f"/v1/runs/{run_id}/events", headers=headers) as r:
        assert r.status_code == 200
        return _messages("".join(r.iter_text()))


@pytest.mark.skipif(not _DUCKDB, reason="duckdb (data extra) not installed")
def test_events_carry_monotonic_ids_and_end_on_run_done(client: TestClient) -> None:
    run_id = client.post("/v1/runs", json={"question": _Q}).json()["id"]
    msgs = _stream(client, run_id)
    ids = [i for i, _ in msgs]
    assert ids == list(range(1, len(ids) + 1))
    assert msgs[0][1] == "run.accepted"
    assert msgs[-1][1] == "run.done"


@pytest.mark.skipif(not _DUCKDB, reason="duckdb (data extra) not installed")
def test_reopening_a_finished_run_replays_everything(client: TestClient) -> None:
    run_id = client.post("/v1/runs", json={"question": _Q}).json()["id"]
    first = _stream(client, run_id)
    # The run finished without any client attached? Either way a second open
    # replays the identical log and closes (it does not re-execute).
    second = _stream(client, run_id)
    assert second == first
    assert not get_run_executor().is_running(run_id)  # type: ignore[arg-type]


@pytest.mark.skipif(not _DUCKDB, reason="duckdb (data extra) not installed")
def test_last_event_id_resumes_from_the_cursor(client: TestClient) -> None:
    run_id = client.post("/v1/runs", json={"question": _Q}).json()["id"]
    full = _stream(client, run_id)
    cursor = full[2][0]
    tail = _stream(client, run_id, **{"Last-Event-ID": str(cursor)})
    assert tail == full[3:]
    assert _stream(client, run_id, **{"Last-Event-ID": "garbage"}) == full
    assert _stream(client, run_id, **{"Last-Event-ID": "999999"}) == []


@pytest.mark.skipif(not _DUCKDB, reason="duckdb (data extra) not installed")
def test_run_completes_without_any_stream_attached(client: TestClient) -> None:
    from uuid import UUID

    run_id = client.post("/v1/runs", json={"question": _Q}).json()["id"]
    executor = get_run_executor()
    # Wait on the app loop for the background task without opening a stream.
    client.portal.call(executor.wait, UUID(run_id))
    r = client.get(f"/v1/runs/{run_id}/report")
    assert r.status_code == 200
    assert r.json()["brief"]["headline"]


def test_capacity_refusal_is_a_429_on_create(client: TestClient) -> None:
    gate = get_concurrency_gate()
    tenant = "c97b860a-9306-59bf-8523-480db6fb3983"  # the dev tenant (uuid5 of dev-tenant.eadip)
    # Fill the tenant's in-flight slots by hand, then try to start a run.
    held = 0
    while gate.try_acquire(tenant):
        held += 1
    try:
        r = client.post("/v1/runs", json={"question": "anything"})
        assert r.status_code == 429
        assert r.headers.get("Retry-After") == "5"
    finally:
        for _ in range(held):
            gate.release(tenant)


@pytest.mark.skipif(not _DUCKDB, reason="duckdb (data extra) not installed")
async def test_concurrent_cold_start_runs_all_stream_to_completion() -> None:
    """Regression: three runs started concurrently on a cold app (no lifespan,
    like the load harness) must each stream to run.done. FastAPI resolves sync
    dependencies in a threadpool; a racing lru_cache miss once produced three
    executors, so two streams saw an 'unknown' run and replayed 5 events."""
    import asyncio
    from uuid import uuid4

    import httpx

    from eadip.gateway import create_app

    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:

        async def one() -> str:
            headers = {"X-Roles": "analyst", "X-Tenant-Id": str(uuid4())}
            created = await client.post("/v1/runs", headers=headers, json={"question": _Q})
            assert created.status_code == 202
            run_id = created.json()["id"]
            async with client.stream(
                "GET", f"/v1/runs/{run_id}/events", headers=headers, timeout=60.0
            ) as stream:
                lines = [line async for line in stream.aiter_lines()]
            events = [line for line in lines if line.startswith("event:")]
            return events[-1] if events else ""

        assert await asyncio.gather(one(), one(), one()) == ["event: run.done"] * 3
