"""EADIP API Gateway (FastAPI): routes, SSE, auth middleware.

Exposes `app` so the container entrypoint `uvicorn eadip.gateway:app` resolves
(TAD §15.1).
"""

from eadip.gateway.app import app, create_app

__all__ = ["app", "create_app"]
