"""Gateway approval endpoints (Phase 9): a remediation run pauses; only an
approver may rule; approve resumes + executes, reject skips. RBAC + audit gated.
Requires the data extra (the run exercises DuckDB analytics)."""

from __future__ import annotations

import importlib.util

import pytest
from fastapi.testclient import TestClient

_DUCKDB = importlib.util.find_spec("duckdb") is not None
_REMEDIATION_Q = (
    "Why did EMEA gross margin fall last quarter? Then open a ticket to remediate the top driver."
)


def _drive(client: TestClient, run_id: str, role: str = "approver") -> None:
    with client.stream("GET", f"/v1/runs/{run_id}/events", headers={"X-Roles": role}) as r:
        "".join(r.iter_text())


@pytest.mark.skipif(not _DUCKDB, reason="duckdb (data extra) not installed")
def test_remediation_run_pauses_with_pending_approval(client: TestClient) -> None:
    rid = client.post(
        "/v1/runs", headers={"X-Roles": "analyst"}, json={"question": _REMEDIATION_Q}
    ).json()["id"]
    _drive(client, rid)
    pending = client.get(f"/v1/runs/{rid}/approvals", headers={"X-Roles": "analyst"}).json()
    assert len(pending) == 1
    assert pending[0]["tool"] == "open_ticket"
    assert pending[0]["effect"] == "write"
    assert pending[0]["payload"]  # the exact payload is shown for review


@pytest.mark.skipif(not _DUCKDB, reason="duckdb (data extra) not installed")
def test_analyst_cannot_approve(client: TestClient) -> None:
    rid = client.post(
        "/v1/runs", headers={"X-Roles": "analyst"}, json={"question": _REMEDIATION_Q}
    ).json()["id"]
    _drive(client, rid)
    aid = client.get(f"/v1/runs/{rid}/approvals", headers={"X-Roles": "analyst"}).json()[0]["id"]
    r = client.post(
        f"/v1/runs/{rid}/approvals/{aid}",
        headers={"X-Roles": "analyst"},
        json={"decision": "approve"},
    )
    assert r.status_code == 403  # authorizing a write needs the approver grant


@pytest.mark.skipif(not _DUCKDB, reason="duckdb (data extra) not installed")
def test_approve_resumes_and_opens_ticket(client: TestClient) -> None:
    rid = client.post(
        "/v1/runs", headers={"X-Roles": "analyst"}, json={"question": _REMEDIATION_Q}
    ).json()["id"]
    _drive(client, rid)
    aid = client.get(f"/v1/runs/{rid}/approvals", headers={"X-Roles": "approver"}).json()[0]["id"]
    dec = client.post(
        f"/v1/runs/{rid}/approvals/{aid}",
        headers={"X-Roles": "approver"},
        json={"decision": "approve"},
    )
    assert dec.status_code == 200 and dec.json()["status"] == "approved"
    _drive(client, rid)  # resume
    brief = client.get(f"/v1/runs/{rid}/report", headers={"X-Roles": "approver"}).json()["brief"]
    tool_claims = [c for c in brief["drill_down"]["claims"] if c["source"] == "tool"]
    assert any("open_ticket" in c["claim"] for c in tool_claims)


@pytest.mark.skipif(not _DUCKDB, reason="duckdb (data extra) not installed")
def test_reject_skips_the_action(client: TestClient) -> None:
    rid = client.post(
        "/v1/runs", headers={"X-Roles": "analyst"}, json={"question": _REMEDIATION_Q}
    ).json()["id"]
    _drive(client, rid)
    aid = client.get(f"/v1/runs/{rid}/approvals", headers={"X-Roles": "approver"}).json()[0]["id"]
    client.post(
        f"/v1/runs/{rid}/approvals/{aid}",
        headers={"X-Roles": "approver"},
        json={"decision": "reject", "reason": "not now"},
    )
    _drive(client, rid)
    brief = client.get(f"/v1/runs/{rid}/report", headers={"X-Roles": "approver"}).json()["brief"]
    tool_claims = [c for c in brief["drill_down"]["claims"] if c["source"] == "tool"]
    assert not tool_claims  # the write was skipped


def test_decision_on_unknown_run_404(client: TestClient) -> None:
    zero = "00000000-0000-0000-0000-000000000000"
    r = client.post(
        f"/v1/runs/{zero}/approvals/{zero}",
        headers={"X-Roles": "approver"},
        json={"decision": "approve"},
    )
    assert r.status_code == 404
