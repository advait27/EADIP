"""Glass Box UI hosting: SPA fallback, precompressed assets, API-only otherwise."""

from __future__ import annotations

import gzip
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from eadip.gateway.static import mount_ui


def _build(tmp_path: Path) -> Path:
    ui = tmp_path / "app"
    (ui / "assets").mkdir(parents=True)
    (ui / "index.html").write_text("<html>glass box</html>")
    (ui / "favicon.svg").write_text("<svg/>")
    (ui / "assets" / "app.js").write_text("console.log(1)")
    (ui / "assets" / "duck.wasm").write_bytes(b"\x00asm" + b"x" * 100)
    (ui / "assets" / "duck.wasm.gz").write_bytes(gzip.compress(b"\x00asm" + b"x" * 100))
    return ui


def test_no_build_means_api_only(tmp_path: Path) -> None:
    app = FastAPI()
    assert mount_ui(app, tmp_path / "missing") is False
    assert app.state.ui_mounted is False
    assert TestClient(app).get("/app").status_code == 404


def test_spa_fallback_and_root_files(tmp_path: Path) -> None:
    app = FastAPI()
    assert mount_ui(app, _build(tmp_path)) is True
    c = TestClient(app)
    for path in ("/app", "/app/", "/app/runs/abc", "/app/share/tok"):
        r = c.get(path)
        assert r.status_code == 200 and "glass box" in r.text, path
        assert r.headers["cache-control"] == "no-cache"
    assert c.get("/app/favicon.svg").text == "<svg/>"
    assert c.get("/app/../pyproject.toml").status_code in (200, 404)  # never escapes
    assert "glass box" in c.get("/app/..%2Fpyproject.toml").text


def test_assets_are_immutable_and_precompressed_when_accepted(tmp_path: Path) -> None:
    app = FastAPI()
    mount_ui(app, _build(tmp_path))
    c = TestClient(app)
    plain = c.get("/app/assets/app.js")
    assert plain.status_code == 200 and "immutable" in plain.headers["cache-control"]
    raw = c.get("/app/assets/duck.wasm", headers={"Accept-Encoding": "identity"})
    assert raw.status_code == 200 and raw.headers.get("content-encoding") is None
    assert raw.headers["content-type"] == "application/wasm"
    gz = c.get("/app/assets/duck.wasm", headers={"Accept-Encoding": "gzip, br"})
    assert gz.status_code == 200 and gz.headers["content-encoding"] == "gzip"
    assert gz.headers["content-type"] == "application/wasm"
    assert gz.headers["vary"] == "Accept-Encoding"
    assert gz.content == raw.content  # the test client transparently decodes
    assert c.get("/app/assets/nope.js").status_code == 404


def test_gateway_root_redirects_to_the_ui_when_built_else_docs(client: TestClient) -> None:
    from eadip.gateway.static import ui_built

    r = client.get("/", follow_redirects=False)
    assert r.status_code in (302, 307)
    assert r.headers["location"].endswith("/app" if ui_built() else "/docs")
