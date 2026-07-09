from __future__ import annotations

from fastapi.testclient import TestClient


def test_healthz(client: TestClient) -> None:
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_readyz(client: TestClient) -> None:
    assert client.get("/readyz").status_code == 200


def test_create_run_returns_202(client: TestClient) -> None:
    r = client.post("/v1/runs", json={"question": "why did EMEA margin fall?"})
    assert r.status_code == 202
    body = r.json()
    assert body["status"] == "queued"
    assert body["question"] == "why did EMEA margin fall?"
    assert "/events" in body["events_url"]


def test_create_run_rejects_empty_question(client: TestClient) -> None:
    assert client.post("/v1/runs", json={"question": ""}).status_code == 422


def test_events_unknown_run_404(client: TestClient) -> None:
    r = client.get("/v1/runs/00000000-0000-0000-0000-000000000000/events")
    assert r.status_code == 404


def test_events_stream_for_created_run(client: TestClient) -> None:
    run_id = client.post("/v1/runs", json={"question": "test"}).json()["id"]
    with client.stream("GET", f"/v1/runs/{run_id}/events") as r:
        assert r.status_code == 200
        body = "".join(r.iter_text())
    assert "accepted" in body
    assert "done" in body


def test_request_id_header_present(client: TestClient) -> None:
    assert client.get("/healthz").headers.get("X-Request-ID")
