"""Gateway /v1/runs: RBAC + the SSE stream IS the orchestration (Phase 6)."""

from __future__ import annotations

import importlib.util

import pytest
from fastapi.testclient import TestClient

from eadip.gateway.dependencies import _dev_audit_log

_DUCKDB = importlib.util.find_spec("duckdb") is not None


def test_create_run_denied_without_run_read(client: TestClient) -> None:
    # compliance has audit/read only, not run/runs read.
    r = client.post("/v1/runs", headers={"X-Roles": "compliance"}, json={"question": "x"})
    assert r.status_code == 403


def test_events_unknown_run_404(client: TestClient) -> None:
    r = client.get("/v1/runs/00000000-0000-0000-0000-000000000000/events")
    assert r.status_code == 404


@pytest.mark.skipif(not _DUCKDB, reason="duckdb (data extra) not installed")
def test_run_streams_plan_steps_and_findings(client: TestClient) -> None:
    audit = _dev_audit_log()
    before = len(audit.events)
    run_id = client.post(
        "/v1/runs", json={"question": "Why did EMEA gross margin fall last quarter?"}
    ).json()["id"]

    with client.stream("GET", f"/v1/runs/{run_id}/events") as r:
        assert r.status_code == 200
        body = "".join(r.iter_text())

    assert "run.accepted" in body
    # Plan preview (US-A2). With long-term memory on (Phase 11), a question this
    # process has already answered for the dev tenant streams plan.reused instead.
    assert "plan.created" in body or "plan.reused" in body
    assert "finding.partial" in body  # streamed partial findings (US-A3)
    assert "run.done" in body
    assert "margin" in body  # the analytics finding came through

    actions = [e.action for e in audit.events[before:]]
    assert "run.create" in actions
    assert "run.stream" in actions
