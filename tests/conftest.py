from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from eadip.gateway import create_app


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())
