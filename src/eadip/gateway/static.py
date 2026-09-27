"""Serve the Glass Box UI (Vite build) from the gateway.

The `web/` build lands in ``src/eadip/gateway/static/app`` (git-ignored, built
in CI and in the Docker image). When that directory exists the SPA is mounted
at ``/app`` with an index fallback for client-side routes, and pre-compressed
siblings (``file.br`` / ``file.gz``, produced by the web build for the large
DuckDB-WASM assets) are served with the matching ``Content-Encoding`` when the
client accepts it. Without a build the gateway is API-only, exactly as before.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import anyio
from fastapi import FastAPI
from starlette.responses import FileResponse, Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

UI_DIR = Path(__file__).parent / "static" / "app"
_ENCODINGS = (("br", ".br"), ("gzip", ".gz"))
_IMMUTABLE = "public, max-age=31536000, immutable"


def _accepted_encodings(accept: str) -> set[str]:
    """Content codings the client accepts, honouring ``q=0`` refusals."""
    accepted: set[str] = set()
    for part in accept.split(","):
        coding, _, params = part.strip().partition(";")
        if not coding:
            continue
        q = 1.0
        for param in params.split(";"):
            key, _, value = param.strip().partition("=")
            if key == "q":
                try:
                    q = float(value)
                except ValueError:
                    q = 0.0
        if q > 0:
            accepted.add(coding)
    return accepted


def _precompressed(full: Path, accept: str) -> tuple[Path, str, str] | None:
    """(compressed sibling of ``full``, encoding, media type of the ORIGINAL) or
    None. ``full`` must already be a contained, existing file (see
    ``PrecompressedStaticFiles.get_response``): this only ever probes ``full``
    plus a known suffix, never the raw request path."""
    accepted = _accepted_encodings(accept)
    for encoding, suffix in _ENCODINGS:
        candidate = full.with_name(full.name + suffix)
        if encoding in accepted and candidate.is_file():
            media_type, _ = _guess_type(full)
            return candidate, encoding, media_type or "application/octet-stream"
    return None


class PrecompressedStaticFiles(StaticFiles):
    """StaticFiles that prefers ``<file>.br`` / ``<file>.gz`` when present.
    Everything under this mount is content-hashed by Vite, hence immutable."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        accept = ""
        for name, value in scope.get("headers", []):
            if name == b"accept-encoding":
                accept = value.decode("latin-1").lower()
        found = None
        if accept:
            # Starlette's lookup_path is the containment check: a request path
            # that escapes the mount resolves to nothing. Siblings are probed
            # only for the file it returns.
            full_path, stat_result = await anyio.to_thread.run_sync(self.lookup_path, path)
            if stat_result is not None and stat.S_ISREG(stat_result.st_mode):
                found = _precompressed(Path(full_path), accept)
        response: Response
        if found is not None:
            candidate, encoding, media_type = found
            response = FileResponse(candidate, media_type=media_type)
            response.headers["Content-Encoding"] = encoding
            response.headers["Vary"] = "Accept-Encoding"
        else:
            response = await super().get_response(path, scope)
        if response.status_code == 200:
            response.headers["Cache-Control"] = _IMMUTABLE
        return response


def _guess_type(path: Path) -> tuple[str | None, str | None]:
    import mimetypes

    if path.suffix == ".wasm":
        return "application/wasm", None
    return mimetypes.guess_type(str(path))


def _root_file(ui_dir: Path, path: str) -> Path | None:
    """A real file directly inside the build dir, or None. Never escapes it."""
    if not path or ".." in path:
        return None
    candidate = ui_dir / path
    if not candidate.is_file():
        return None
    root = str(ui_dir.resolve())
    if os.path.commonpath([root, str(candidate.resolve())]) != root:
        return None
    return candidate


def ui_built(ui_dir: Path = UI_DIR) -> bool:
    return (ui_dir / "index.html").is_file()


def mount_ui(app: FastAPI, ui_dir: Path = UI_DIR) -> bool:
    """Mount the SPA at /app when a build exists. Returns whether it did."""
    if not ui_built(ui_dir):
        app.state.ui_mounted = False
        return False
    index = ui_dir / "index.html"
    assets = ui_dir / "assets"
    if assets.is_dir():
        app.mount("/app/assets", PrecompressedStaticFiles(directory=str(assets)), name="ui-assets")

    @app.get("/app", include_in_schema=False)
    @app.get("/app/{path:path}", include_in_schema=False)
    async def spa(path: str = "") -> Response:
        # Real files at the root of the build (favicon, manifest) are served as
        # such; everything else is a client-side route -> index.html.
        root_file = _root_file(ui_dir, path)
        if root_file is not None:
            return FileResponse(root_file)
        return FileResponse(index, headers={"Cache-Control": "no-cache"})

    app.state.ui_mounted = True
    return True
