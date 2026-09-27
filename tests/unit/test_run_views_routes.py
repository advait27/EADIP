"""Glass Box run views: status, timeline, graph, evidence bundle (RBAC + audit)."""

from __future__ import annotations

import importlib.util

import pytest
from fastapi.testclient import TestClient

from eadip.gateway.dependencies import _dev_audit_log

_DUCKDB = importlib.util.find_spec("duckdb") is not None
_Q = "Why did EMEA gross margin fall last quarter?"
_ZERO = "00000000-0000-0000-0000-000000000000"


def _finish(client: TestClient, question: str = _Q) -> str:
    run_id = client.post("/v1/runs", json={"question": question}).json()["id"]
    with client.stream("GET", f"/v1/runs/{run_id}/events") as r:
        "".join(r.iter_text())
    return run_id


@pytest.mark.parametrize("path", ["", "/timeline", "/graph", "/evidence-bundle"])
def test_views_404_for_unknown_run(client: TestClient, path: str) -> None:
    assert client.get(f"/v1/runs/{_ZERO}{path}").status_code == 404


@pytest.mark.parametrize("path", ["", "/timeline", "/graph", "/evidence-bundle"])
def test_views_denied_without_run_read(client: TestClient, path: str) -> None:
    r = client.get(f"/v1/runs/{_ZERO}{path}", headers={"X-Roles": "compliance"})
    assert r.status_code == 403


def test_status_of_a_fresh_run(client: TestClient) -> None:
    run_id = client.post("/v1/runs", json={"question": "hello"}).json()["id"]
    body = client.get(f"/v1/runs/{run_id}").json()
    assert body["question"] == "hello"
    assert body["status"] in ("queued", "planning", "executing", "verifying", "done", "failed")
    assert body["claims"] == {"verified": 0, "unverified": 0, "conflicting": 0} or body["has_brief"]


@pytest.mark.skipif(not _DUCKDB, reason="duckdb (data extra) not installed")
def test_status_timeline_graph_after_completion(client: TestClient) -> None:
    audit = _dev_audit_log()
    before = len(audit.events)
    run_id = _finish(client)

    status = client.get(f"/v1/runs/{run_id}").json()
    assert status["status"] == "done" and status["running"] is False
    assert status["has_brief"] and status["claims"]["verified"] > 0
    assert status["findings"] > 0 and status["pending_approvals"] == 0

    timeline = client.get(f"/v1/runs/{run_id}/timeline").json()
    assert timeline["question"] == _Q and timeline["status"] == "done"
    seqs = [e["seq"] for e in timeline["events"]]
    assert seqs == list(range(1, len(seqs) + 1)) and seqs[-1] == status["last_seq"]
    assert timeline["events"][0]["type"] == "run.accepted"
    assert timeline["events"][-1]["type"] == "run.done"
    assert all(isinstance(e["at"], float) for e in timeline["events"])

    graph = client.get(f"/v1/runs/{run_id}/graph").json()
    kinds = {n["kind"] for n in graph["nodes"]}
    assert {"question", "goal", "step", "finding", "claim", "evidence"} <= kinds
    ids = {n["id"] for n in graph["nodes"]}
    assert all(e["source"] in ids and e["target"] in ids for e in graph["edges"])
    assert any(e["relation"] == "verifies" for e in graph["edges"])

    actions = [e.action for e in audit.events[before:]]
    assert "run.timeline" in actions and "run.graph" in actions


@pytest.mark.skipif(not _DUCKDB, reason="duckdb (data extra) not installed")
def test_evidence_bundle_after_completion(client: TestClient) -> None:
    run_id = _finish(client)
    bundle = client.get(f"/v1/runs/{run_id}/evidence-bundle").json()
    assert bundle["run_id"] == run_id
    assert bundle["claims"], "at least one analytics claim with SQL provenance"
    assert all("tenant_id = '" in c["sql"] for c in bundle["claims"])
    [table] = bundle["tables"]
    assert table["name"] == "finance_metrics" and table["truncated"] is False
    assert table["row_count"] == 54  # the demo dataset for one tenant
    names = [c["name"] for c in table["columns"]]
    assert names == ["tenant_id", "region", "product_line", "period", "revenue", "cogs", "opex"]
    assert len(table["rows"][0]) == len(names)
    # Every claim's tables are present in the bundle.
    present = {t["name"] for t in bundle["tables"]}
    assert all(set(c["tables"]) <= present for c in bundle["claims"])
    # Claim indices line up with the graph's claim:<n> ids.
    graph = client.get(f"/v1/runs/{run_id}/graph").json()
    claim_ids = {n["id"] for n in graph["nodes"] if n["kind"] == "claim"}
    assert all(f"claim:{c['index']}" in claim_ids for c in bundle["claims"])


@pytest.mark.skipif(not _DUCKDB, reason="duckdb (data extra) not installed")
def test_views_are_tenant_scoped(client: TestClient) -> None:
    run_id = _finish(client)
    other = {"X-Tenant-ID": "11111111-1111-1111-1111-111111111111"}
    for path in ("", "/timeline", "/graph", "/evidence-bundle"):
        assert client.get(f"/v1/runs/{run_id}{path}", headers=other).status_code == 404
