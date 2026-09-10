"""Share links (Glass Box): scoped, signed, expiring, read-only, residency-checked."""

from __future__ import annotations

import importlib.util
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from eadip.gateway.dependencies import _dev_audit_log, get_residency_policy, get_token_issuer
from eadip.security.identity import Identity

_DUCKDB = importlib.util.find_spec("duckdb") is not None
_Q = "Why did EMEA gross margin fall last quarter?"
_DEV_TENANT = UUID("c97b860a-9306-59bf-8523-480db6fb3983")


def _finish(client: TestClient) -> str:
    run_id = client.post("/v1/runs", json={"question": _Q}).json()["id"]
    with client.stream("GET", f"/v1/runs/{run_id}/events") as r:
        "".join(r.iter_text())
    return run_id


def _no_auth() -> TestClient:
    """A client that sends NO identity headers at all (still dev auth mode, so a
    default identity would apply to authenticated routes — the share routes must
    not rely on it: they authorise from the token alone)."""
    from eadip.gateway import create_app

    return TestClient(create_app())


def test_share_unknown_run_404(client: TestClient) -> None:
    assert client.post("/v1/runs/00000000-0000-0000-0000-000000000000/share").status_code == 404


def test_share_denied_without_run_read(client: TestClient) -> None:
    r = client.post(
        "/v1/runs/00000000-0000-0000-0000-000000000000/share", headers={"X-Roles": "compliance"}
    )
    assert r.status_code == 403


@pytest.mark.skipif(not _DUCKDB, reason="duckdb (data extra) not installed")
def test_share_link_replays_without_identity(client: TestClient) -> None:
    audit = _dev_audit_log()
    run_id = _finish(client)
    before = len(audit.events)
    share = client.post(f"/v1/runs/{run_id}/share").json()
    assert share["token"] and share["expires_at"]
    assert share["api_url"].endswith(f"/v1/share/{share['token']}")

    view = client.get(f"/v1/share/{share['token']}", headers={"X-Roles": "", "X-Tenant-ID": ""})
    assert view.status_code == 200
    body = view.json()
    assert body["run_id"] == run_id and body["question"] == _Q and body["status"] == "done"
    assert body["timeline"][-1]["type"] == "run.done"
    assert any(n["kind"] == "claim" for n in body["graph"]["nodes"])
    assert body["brief"]["headline"]
    assert body["evidence_bundle_url"].endswith(f"/v1/share/{share['token']}/evidence-bundle")

    bundle = client.get(body["evidence_bundle_url"]).json()
    assert bundle["claims"] and bundle["tables"][0]["name"] == "finance_metrics"

    actions = [e.action for e in audit.events[before:]]
    assert "run.share.create" in actions
    assert "run.share.view" in actions and "run.share.evidence_bundle" in actions
    viewer = next(e for e in audit.events[before:] if e.action == "run.share.view")
    assert viewer.actor.startswith("share:") and viewer.tenant_id == _DEV_TENANT


@pytest.mark.skipif(not _DUCKDB, reason="duckdb (data extra) not installed")
def test_share_link_is_read_only_and_tenant_bound(client: TestClient) -> None:
    run_id = _finish(client)
    token = client.post(f"/v1/runs/{run_id}/share").json()["token"]
    # Another tenant's identity headers make no difference: the token decides.
    other = {"X-Tenant-ID": str(uuid4()), "X-Roles": "analyst"}
    assert client.get(f"/v1/share/{token}", headers=other).status_code == 200
    # The token grants nothing on authenticated routes.
    assert (
        client.get(
            f"/v1/runs/{run_id}", headers={"Authorization": f"Bearer {token}", **other}
        ).status_code
        == 404
    )


def test_tampered_garbage_and_wrong_scope_tokens_are_404(client: TestClient) -> None:
    issuer = get_token_issuer()
    run_id = client.post("/v1/runs", json={"question": "x"}).json()["id"]
    good = client.post(f"/v1/runs/{run_id}/share").json()["token"]
    assert client.get(f"/v1/share/{good[:-4]}zzzz").status_code == 404
    assert client.get("/v1/share/not-a-token").status_code == 404
    # A plain service JWT (same key, same issuer) must NOT open a replay.
    service = issuer.mint(Identity(user_id=uuid4(), tenant_id=_DEV_TENANT, roles=("admin",)))
    assert client.get(f"/v1/share/{service}").status_code == 404
    # Right scope, unknown run.
    orphan = issuer.mint_claims(
        {"scope": "replay", "run": str(uuid4()), "tenant": str(_DEV_TENANT), "jti": "x"}
    )
    assert client.get(f"/v1/share/{orphan}").status_code == 404
    # Right scope, wrong tenant for a real run.
    cross = issuer.mint_claims(
        {"scope": "replay", "run": run_id, "tenant": str(uuid4()), "jti": "x"}
    )
    assert client.get(f"/v1/share/{cross}").status_code == 404


def test_expired_token_is_404(client: TestClient) -> None:
    run_id = client.post("/v1/runs", json={"question": "x"}).json()["id"]
    expired = get_token_issuer().mint_claims(
        {"scope": "replay", "run": run_id, "tenant": str(_DEV_TENANT), "jti": "x"}, ttl_s=-5
    )
    assert client.get(f"/v1/share/{expired}").status_code == 404


async def test_share_view_respects_residency(client: TestClient) -> None:
    tenant = uuid4()
    headers = {"X-Tenant-ID": str(tenant)}
    run_id = client.post("/v1/runs", headers=headers, json={"question": "x"}).json()["id"]
    token = client.post(f"/v1/runs/{run_id}/share", headers=headers).json()["token"]
    assert client.get(f"/v1/share/{token}").status_code == 200
    await get_residency_policy().pin(tenant, "eu-west-1")
    try:
        assert client.get(f"/v1/share/{token}").status_code == 451
        assert client.get(f"/v1/share/{token}/evidence-bundle").status_code == 451
    finally:
        await get_residency_policy().pin(tenant, get_residency_policy().deployment_region)
