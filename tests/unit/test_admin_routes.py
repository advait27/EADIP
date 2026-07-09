"""Administration Portal /v1/admin (Phase 11): admin-only access (deny-by-default),
prompt lifecycle over HTTP (eval gate + canary + rollback), guardrailed routing
overrides, budgets, flags, memory governance and the notification inbox."""

from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient

ADMIN = {"X-Roles": "admin"}
ANALYST = {"X-Roles": "analyst"}


# --- access control -------------------------------------------------------------
def test_admin_surface_denied_for_non_admin_roles(client: TestClient) -> None:
    assert client.get("/v1/admin/roles", headers=ANALYST).status_code == 403
    assert client.get("/v1/admin/prompts", headers=ANALYST).status_code == 403
    assert client.get("/v1/admin/routing", headers={"X-Roles": "approver"}).status_code == 403
    assert client.get("/v1/admin/budgets", headers={"X-Roles": "viewer"}).status_code == 403
    assert client.delete(f"/v1/admin/memory/{uuid4()}", headers=ANALYST).status_code == 403


def test_role_catalog_listing_and_custom_role(client: TestClient) -> None:
    r = client.get("/v1/admin/roles", headers=ADMIN)
    assert r.status_code == 200
    names = {item["name"] for item in r.json()}
    assert {"analyst", "approver", "viewer", "compliance", "admin"} <= names

    # Built-in roles are immutable; custom roles are explicit grants only.
    conflict = client.post(
        "/v1/admin/roles", headers=ADMIN, json={"name": "analyst", "permissions": ["run/runs/read"]}
    )
    assert conflict.status_code == 409
    bad = client.post(
        "/v1/admin/roles", headers=ADMIN, json={"name": "auditor2", "permissions": ["nonsense"]}
    )
    assert bad.status_code == 422
    created = client.post(
        "/v1/admin/roles",
        headers=ADMIN,
        json={"name": "kb-reader", "permissions": ["knowledge/*/read"]},
    )
    assert created.status_code == 201
    assert created.json()["permissions"] == ["knowledge/*/read"]
    listed = {item["name"] for item in client.get("/v1/admin/roles", headers=ADMIN).json()}
    assert "kb-reader" in listed
    # The new role takes effect immediately in the live PDP: knowledge search is
    # now permitted, while anything else stays deny-by-default.
    search = client.post("/v1/search", headers={"X-Roles": "kb-reader"}, json={"query": "margin"})
    assert search.status_code == 200
    runs = client.post("/v1/runs", headers={"X-Roles": "kb-reader"}, json={"question": "x"})
    assert runs.status_code == 403


# --- prompt lifecycle (US-D2) ------------------------------------------------------
def test_prompt_lifecycle_register_eval_gate_promote_rollback(client: TestClient) -> None:
    name = f"agent/test-{uuid4().hex[:8]}"
    v1 = client.post("/v1/admin/prompts", headers=ADMIN, json={"name": name, "content": "one"})
    assert v1.status_code == 201 and v1.json()["version"] == 1

    # Promotion without a recorded eval is refused (the gate).
    denied = client.post(f"/v1/admin/prompts/{name}/versions/1/promote", headers=ADMIN)
    assert denied.status_code == 409 and "no recorded eval" in denied.json()["detail"]

    # Low score still refused; passing score promotes.
    client.post(f"/v1/admin/prompts/{name}/versions/1/eval", headers=ADMIN, json={"score": 0.5})
    low = client.post(f"/v1/admin/prompts/{name}/versions/1/promote", headers=ADMIN)
    assert low.status_code == 409 and "below the promotion threshold" in low.json()["detail"]
    client.post(f"/v1/admin/prompts/{name}/versions/1/eval", headers=ADMIN, json={"score": 0.97})
    promoted = client.post(f"/v1/admin/prompts/{name}/versions/1/promote", headers=ADMIN)
    assert promoted.status_code == 200 and promoted.json()["stage"] == "active"

    # v2: eval-passed canary, then full promotion, then one-click rollback.
    client.post("/v1/admin/prompts", headers=ADMIN, json={"name": name, "content": "two"})
    client.post(f"/v1/admin/prompts/{name}/versions/2/eval", headers=ADMIN, json={"score": 0.92})
    canary = client.post(
        f"/v1/admin/prompts/{name}/versions/2/canary", headers=ADMIN, json={"fraction": 0.25}
    )
    assert canary.status_code == 200 and canary.json()["stage"] == "candidate"
    resolved = client.get(f"/v1/admin/prompts/{name}/resolve", headers=ADMIN)
    assert resolved.status_code == 200 and resolved.json()["version"] == 1  # no key -> active
    client.post(f"/v1/admin/prompts/{name}/versions/2/promote", headers=ADMIN)
    rolled = client.post(f"/v1/admin/prompts/{name}/rollback", headers=ADMIN)
    assert rolled.status_code == 200
    assert rolled.json()["version"] == 1 and rolled.json()["stage"] == "active"


# --- model routing (US-D3) -----------------------------------------------------------
def test_routing_table_and_guardrailed_override(client: TestClient) -> None:
    table = client.get("/v1/admin/routing", headers=ADMIN)
    assert table.status_code == 200
    by_task = {row["task"]: row for row in table.json()}
    assert by_task["plan"]["pinned"] and by_task["plan"]["tier"] == "strong"
    assert by_task["verify"]["pinned"] and by_task["verify"]["tier"] == "strong"

    # Unpinning verification is refused (guardrail), other overrides apply.
    refused = client.put("/v1/admin/routing/verify", headers=ADMIN, json={"tier": "light"})
    assert refused.status_code == 422
    unknown = client.put("/v1/admin/routing/nonsense", headers=ADMIN, json={"tier": "light"})
    assert unknown.status_code == 404
    changed = client.put("/v1/admin/routing/rerank", headers=ADMIN, json={"tier": "standard"})
    assert changed.status_code == 200 and changed.json()["tier"] == "standard"


# --- budgets, flags -------------------------------------------------------------------
def test_budget_set_and_read(client: TestClient) -> None:
    tenant = uuid4()
    put = client.put(
        f"/v1/admin/tenants/{tenant}/budget", headers=ADMIN, json={"monthly_cap_usd": 25.0}
    )
    assert put.status_code == 200 and put.json()["monthly_cap_usd"] == 25.0
    got = client.get(f"/v1/admin/tenants/{tenant}/budget", headers=ADMIN)
    assert got.status_code == 200 and not got.json()["exhausted"]
    listed = client.get("/v1/admin/budgets", headers=ADMIN)
    assert any(row["tenant_id"] == str(tenant) for row in listed.json())


def test_flags_runtime_toggle(client: TestClient) -> None:
    flag = f"flag_{uuid4().hex[:8]}"
    r = client.put(f"/v1/admin/flags/{flag}", headers=ADMIN, json={"enabled": True})
    assert r.status_code == 200 and r.json()[flag] is True
    r = client.put(f"/v1/admin/flags/{flag}", headers=ADMIN, json={"enabled": False})
    assert r.json()[flag] is False


# --- memory governance + notifications ---------------------------------------------------
def test_memory_status_and_erasure_cascade(client: TestClient) -> None:
    tenant = uuid4()
    status = client.get(f"/v1/admin/memory/{tenant}", headers=ADMIN)
    assert status.status_code == 200
    assert status.json() == {"tenant_id": str(tenant), "episodic": 0, "semantic": 0}
    erased = client.delete(f"/v1/admin/memory/{tenant}", headers=ADMIN)
    assert erased.status_code == 200
    assert erased.json()["episodic_erased"] == 0 and erased.json()["semantic_erased"] == 0


def test_notification_inbox_empty_for_fresh_tenant(client: TestClient) -> None:
    r = client.get(f"/v1/admin/notifications/{uuid4()}", headers=ADMIN)
    assert r.status_code == 200 and r.json() == []
