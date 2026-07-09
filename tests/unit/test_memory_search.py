from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from eadip.adapters.memory_vector_store import InMemoryVectorStore
from eadip.ports.embeddings import SparseVector
from eadip.ports.vector_store import SearchFilter, VectorPoint


def _point(
    *, acl: tuple[str, ...] = (), vf: str | None = None, vt: str | None = None, text: str = "t"
) -> VectorPoint:
    return VectorPoint(
        id=uuid4(),
        dense=[1.0, 0.0],
        sparse=SparseVector(indices=[1], values=[1.0]),
        payload={
            "text": text,
            "acl_tags": list(acl),
            "valid_from": vf,
            "valid_to": vt,
            "source_type": "document",
            "source_ref": "r",
        },
    )


async def test_acl_filter_excludes_restricted_docs() -> None:
    vs = InMemoryVectorStore()
    tenant = uuid4()
    await vs.ensure_collection(tenant, dimension=2)
    public = _point(text="public")
    restricted = _point(acl=("board",), text="secret")
    await vs.upsert(tenant, [public, restricted])

    found = await vs.search_dense(
        tenant, vector=[1.0, 0.0], flt=SearchFilter(acl_tags=("finance",)), limit=10
    )
    ids = {p.id for p in found}
    assert public.id in ids
    assert restricted.id not in ids  # never surfaces / never influences ranking


async def test_acl_filter_includes_on_match() -> None:
    vs = InMemoryVectorStore()
    tenant = uuid4()
    restricted = _point(acl=("board",), text="secret")
    await vs.upsert(tenant, [restricted])
    found = await vs.search_dense(
        tenant, vector=[1.0, 0.0], flt=SearchFilter(acl_tags=("board",)), limit=10
    )
    assert {p.id for p in found} == {restricted.id}


async def test_time_aware_as_of() -> None:
    vs = InMemoryVectorStore()
    tenant = uuid4()
    old = _point(vf="2025-01-01T00:00:00+00:00", vt="2025-12-31T00:00:00+00:00", text="old")
    current = _point(vf="2026-01-01T00:00:00+00:00", text="current")
    await vs.upsert(tenant, [old, current])

    flt = SearchFilter(as_of=datetime(2026, 6, 1, tzinfo=UTC))
    found = await vs.search_dense(tenant, vector=[1.0, 0.0], flt=flt, limit=10)
    assert {p.id for p in found} == {current.id}
