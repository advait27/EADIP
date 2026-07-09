"""Gateway GET /v1/runs/{id}/report — the verified executive brief (Phase 7)."""

from __future__ import annotations

import importlib.util

import pytest
from fastapi.testclient import TestClient

_DUCKDB = importlib.util.find_spec("duckdb") is not None


def test_report_denied_without_run_read(client: TestClient) -> None:
    r = client.get(
        "/v1/runs/00000000-0000-0000-0000-000000000000/report", headers={"X-Roles": "compliance"}
    )
    assert r.status_code == 403


def test_report_unknown_run_404(client: TestClient) -> None:
    r = client.get("/v1/runs/00000000-0000-0000-0000-000000000000/report")
    assert r.status_code == 404


@pytest.mark.skipif(not _DUCKDB, reason="duckdb (data extra) not installed")
def test_report_after_run_returns_verified_brief(client: TestClient) -> None:
    run_id = client.post(
        "/v1/runs", json={"question": "Why did EMEA gross margin fall last quarter?"}
    ).json()["id"]
    # Drive the orchestration to completion by consuming the SSE stream.
    with client.stream("GET", f"/v1/runs/{run_id}/events") as r:
        "".join(r.iter_text())

    resp = client.get(f"/v1/runs/{run_id}/report")
    assert resp.status_code == 200
    body = resp.json()
    assert body["verified"] > 0
    assert "EMEA" in body["brief"]["headline"]
    assert body["brief"]["recommendations"]  # actionable output
    assert "claims" in body["brief"]["drill_down"]  # drill-down layer
