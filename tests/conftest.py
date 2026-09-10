from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from eadip.gateway import create_app


@pytest.fixture
def client() -> Iterator[TestClient]:
    # Context-managed so the app's lifespan runs and ONE event loop spans every
    # request in a test: runs execute as background tasks (Glass Box) and the
    # SSE stream tails them on that same loop.
    with TestClient(create_app()) as c:
        yield c
