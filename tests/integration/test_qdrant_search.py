"""Qdrant hybrid search + ACL filter translation (FR-010, AP-5).

Skipped without a live Qdrant; CI runs it. Validates that the Qdrant filter
matches the in-memory reference semantics (restricted docs excluded pre-ANN).
"""

from __future__ import annotations

import os
from uuid import uuid4

import pytest
import pytest_asyncio

from eadip.adapters.qdrant_store import QdrantVectorStore
from eadip.ports.embeddings import SparseVector
from eadip.ports.vector_store import SearchFilter, VectorPoint

URL = os.environ.get("EADIP_TEST_QDRANT_URL", "http://localhost:6333")


@pytest_asyncio.fixture
async def store() -> QdrantVectorStore:
    vs = QdrantVectorStore(URL, prefix="isearch")
    try:
        await vs._client_or_connect().get_collections()
    except Exception:
        pytest.skip("Qdrant not available for integration tests")
    yield vs
    await vs.close()


def _point(acl: list[str], text: str) -> VectorPoint:
    return VectorPoint(
        id=uuid4(),
        dense=[0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8],
        sparse=SparseVector(indices=[1, 5], values=[1.0, 2.0]),
        payload={"text": text, "acl_tags": acl, "source_type": "document", "source_ref": "r"},
    )


async def test_dense_search_excludes_restricted(store: QdrantVectorStore) -> None:
    tenant = uuid4()
    await store.ensure_collection(tenant, dimension=8)
    public = _point([], "public commentary")
    restricted = _point(["board"], "confidential memo")
    await store.upsert(tenant, [public, restricted])

    found = await store.search_dense(
        tenant,
        vector=[0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8],
        flt=SearchFilter(acl_tags=("finance",)),
        limit=10,
    )
    ids = {p.id for p in found}
    assert public.id in ids
    assert restricted.id not in ids
