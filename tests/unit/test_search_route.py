"""Gateway /v1/search: governed (RBAC) + audited retrieval surface."""

from __future__ import annotations

from fastapi.testclient import TestClient

from eadip.gateway.dependencies import _dev_audit_log


def test_search_denied_without_knowledge_read(client: TestClient) -> None:
    # compliance role has audit/read only, not knowledge/read.
    r = client.post("/v1/search", headers={"X-Roles": "compliance"}, json={"query": "x"})
    assert r.status_code == 403


def test_search_allowed_for_analyst_and_audited(client: TestClient) -> None:
    audit = _dev_audit_log()
    before = len(audit.events)
    r = client.post(
        "/v1/search", headers={"X-Roles": "analyst"}, json={"query": "why did EMEA margin fall"}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["query"] == "why did EMEA margin fall"
    assert isinstance(body["evidence"], list)
    actions = [e.action for e in audit.events[before:]]
    assert "knowledge.search" in actions


def test_search_rejects_empty_query(client: TestClient) -> None:
    r = client.post("/v1/search", headers={"X-Roles": "analyst"}, json={"query": ""})
    assert r.status_code == 422
