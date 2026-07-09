"""Gateway /v1/analytics: governed (RBAC) + audited NL->SQL analytics surface."""

from __future__ import annotations

import importlib.util

import pytest
from fastapi.testclient import TestClient

from eadip.gateway.dependencies import _dev_audit_log

_DUCKDB = importlib.util.find_spec("duckdb") is not None


def test_analytics_denied_without_metrics_read(client: TestClient) -> None:
    # compliance has audit/read only, not metrics/read.
    r = client.post("/v1/analytics", headers={"X-Roles": "compliance"}, json={"question": "x"})
    assert r.status_code == 403


def test_analytics_rejects_empty_question(client: TestClient) -> None:
    r = client.post("/v1/analytics", headers={"X-Roles": "analyst"}, json={"question": ""})
    assert r.status_code == 422


@pytest.mark.skipif(not _DUCKDB, reason="duckdb (data extra) not installed")
def test_analytics_allowed_for_analyst_and_audited(client: TestClient) -> None:
    audit = _dev_audit_log()
    before = len(audit.events)
    r = client.post(
        "/v1/analytics",
        headers={"X-Roles": "analyst"},
        json={"question": "Why did EMEA gross margin fall last quarter?"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["verified"] is True
    assert "EMEA" in body["headline"]
    assert body["queries"], "queries (provenance) should be returned"
    assert all(f["evidence_sql"] for f in body["findings"])  # every claim has a query

    actions = [e.action for e in audit.events[before:]]
    assert "authz.decision" in actions
    assert "analytics.query" in actions
    assert "sql.execute" in actions  # each executed statement is audited
