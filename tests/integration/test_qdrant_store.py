"""Qdrant integration: per-tenant collections + isolation (FR-016, AP-5).

Skipped when Qdrant is unreachable or qdrant-client (the `data` extra) is not
installed. CI runs it against a Qdrant service. Override with EADIP_TEST_QDRANT_URL.
"""

from __future__ import annotations

import os
from uuid import uuid4

import pytest
import pytest_asyncio

from eadip.adapters.qdrant_store import QdrantVectorStore
from eadip.ports.embeddings import SparseVector
from eadip.ports.vector_store import VectorPoint

URL = os.environ.get("EADIP_TEST_QDRANT_URL", "http://localhost:6333")


@pytest_asyncio.fixture
async def store() -> QdrantVectorStore:
    vs = QdrantVectorStore(URL, prefix="itest")
    try:
        await vs._client_or_connect().get_collections()
    except Exception:
        pytest.skip("Qdrant not available for integration tests")
    yield vs
    await vs.close()


async def test_per_tenant_collections_and_count(store: QdrantVectorStore) -> None:
    tenant_a, tenant_b = uuid4(), uuid4()
    dim = 8
    await store.ensure_collection(tenant_a, dimension=dim)
    await store.ensure_collection(tenant_b, dimension=dim)

    point = VectorPoint(
        id=uuid4(),
        dense=[0.1] * dim,
        sparse=SparseVector(indices=[1, 2], values=[1.0, 2.0]),
        payload={
            "acl_tags": ["finance"],
            "valid_from": "2026-01-01T00:00:00+00:00",
            "text": "redacted body",
        },
    )
    await store.upsert(tenant_a, [point])

    assert await store.count(tenant_a) == 1
    assert await store.count(tenant_b) == 0  # separate per-tenant collection
