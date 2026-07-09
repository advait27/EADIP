"""Gateway authorization: every decision is enforced and audited (SEC-03/08)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from eadip.gateway.dependencies import _dev_audit_log


def test_analyst_can_create_run_and_is_audited(client: TestClient) -> None:
    audit = _dev_audit_log()
    before = len(audit.events)
    r = client.post("/v1/runs", headers={"X-Roles": "analyst"}, json={"question": "q"})
    assert r.status_code == 202
    new = audit.events[before:]
    actions = [e.action for e in new]
    assert "authz.decision" in actions
    assert "run.create" in actions
    authz = next(e for e in new if e.action == "authz.decision")
    assert authz.detail["permit"] is True


def test_role_without_permission_is_denied_and_audited(client: TestClient) -> None:
    audit = _dev_audit_log()
    before = len(audit.events)
    r = client.post("/v1/runs", headers={"X-Roles": "compliance"}, json={"question": "q"})
    assert r.status_code == 403
    new = audit.events[before:]
    authz = next(e for e in new if e.action == "authz.decision")
    assert authz.detail["permit"] is False
    # A denied request must not create a run.
    assert all(e.action != "run.create" for e in new)


def test_unknown_role_is_denied(client: TestClient) -> None:
    r = client.post("/v1/runs", headers={"X-Roles": "nobody"}, json={"question": "q"})
    assert r.status_code == 403
