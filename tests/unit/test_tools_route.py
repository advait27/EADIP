"""Gateway /v1/tools (Phase 8): register → discover → health → invoke, all RBAC-
gated + audited. Exercises the deterministic demo server seeded per tenant."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_list_seeds_and_discovers_demo_server(client: TestClient) -> None:
    r = client.get("/v1/tools", headers={"X-Roles": "analyst"})
    assert r.status_code == 200
    servers = r.json()
    assert servers and servers[0]["name"] == "enterprise-tools"
    tool_names = {t["name"] for t in servers[0]["tools"]}
    assert {"fx_rate", "open_ticket"} <= tool_names
    # Cached schemas are surfaced (discovery).
    fx = next(t for t in servers[0]["tools"] if t["name"] == "fx_rate")
    assert fx["input_schema"]["required"] == ["currency"]
    assert fx["effect"] == "read"


def test_read_tool_invocable_by_analyst_within_permissions(client: TestClient) -> None:
    r = client.post(
        "/v1/tools/enterprise-tools/fx_rate/invoke",
        headers={"X-Roles": "analyst"},
        json={"arguments": {"currency": "EUR"}},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["output"]["untrusted"] is True  # SEC-06 envelope
    assert body["output"]["data"]["usd_rate"] == 1.08


def test_write_tool_denied_for_analyst(client: TestClient) -> None:
    r = client.post(
        "/v1/tools/enterprise-tools/open_ticket/invoke",
        headers={"X-Roles": "analyst"},
        json={"arguments": {"title": "Investigate the margin drop"}},
    )
    assert r.status_code == 403


def test_write_tool_allowed_for_approver(client: TestClient) -> None:
    r = client.post(
        "/v1/tools/enterprise-tools/open_ticket/invoke",
        headers={"X-Roles": "approver"},
        json={"arguments": {"title": "Investigate the margin drop", "priority": "high"}},
    )
    assert r.status_code == 200 and r.json()["ok"] is True


def test_invalid_arguments_are_refused(client: TestClient) -> None:
    r = client.post(
        "/v1/tools/enterprise-tools/fx_rate/invoke",
        headers={"X-Roles": "analyst"},
        json={"arguments": {"currency": "XXX"}},
    )
    assert r.status_code == 200 and r.json()["reason"] == "invalid_arguments"


def test_register_requires_admin_write(client: TestClient) -> None:
    body = {"name": "my-crm", "transport": "inprocess"}
    # analyst cannot onboard connectors (tool/*/write is approver/admin).
    assert client.post("/v1/tools", headers={"X-Roles": "analyst"}, json=body).status_code == 403


def test_register_and_health_check_as_admin(client: TestClient) -> None:
    # Admin registers the (in-process demo) server under a new name — no deploy.
    reg = client.post(
        "/v1/tools",
        headers={"X-Roles": "admin"},
        json={"name": "enterprise-tools", "transport": "inprocess"},
    )
    assert reg.status_code == 201
    assert reg.json()["health"]["state"] == "healthy"
    health = client.post("/v1/tools/enterprise-tools/health", headers={"X-Roles": "admin"})
    assert health.status_code == 200 and health.json()["state"] == "healthy"


def test_health_check_unknown_server_404(client: TestClient) -> None:
    r = client.post("/v1/tools/nope/health", headers={"X-Roles": "analyst"})
    assert r.status_code == 404


def test_invoke_unknown_tool_404(client: TestClient) -> None:
    r = client.post(
        "/v1/tools/enterprise-tools/nope/invoke", headers={"X-Roles": "analyst"}, json={}
    )
    assert r.status_code == 404
