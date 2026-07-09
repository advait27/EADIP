"""In-memory vector store for dev/tests.

Mirrors the Qdrant adapter's per-tenant-collection semantics — including the
pre-ANN ACL/time/source filter — so the ingestion + retrieval pipeline runs
end-to-end with no external service. This is the authoritative reference for the
filter semantics the Qdrant adapter must match.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from datetime import datetime
from uuid import UUID

from eadip.ports.embeddings import DenseVector, SparseVector
from eadip.ports.vector_store import ScoredPoint, SearchFilter, VectorPoint


def _parse_dt(value: object) -> datetime | None:
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _visible(payload: dict, flt: SearchFilter) -> bool:
    # ACL: a point with no acl_tags is public within the tenant; a tagged point
    # requires the caller to hold at least one matching tag (deny-by-default, AP-5).
    point_tags = set(payload.get("acl_tags") or [])
    if point_tags:
        if not flt.acl_tags or point_tags.isdisjoint(flt.acl_tags):
            return False
    # Source restriction (by source_type or source_ref).
    if flt.sources:
        if (
            payload.get("source_type") not in flt.sources
            and payload.get("source_ref") not in flt.sources
        ):
            return False
    # Time-aware: valid_from <= as_of < valid_to (nulls are open-ended).
    if flt.as_of is not None:
        valid_from = _parse_dt(payload.get("valid_from"))
        valid_to = _parse_dt(payload.get("valid_to"))
        if valid_from is not None and valid_from > flt.as_of:
            return False
        if valid_to is not None and flt.as_of >= valid_to:
            return False
    return True


def _cosine(a: DenseVector, b: DenseVector) -> float:
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _sparse_dot(q: SparseVector, p: SparseVector) -> float:
    pv = dict(zip(p.indices, p.values, strict=True))
    return sum(value * pv.get(idx, 0.0) for idx, value in zip(q.indices, q.values, strict=True))


class InMemoryVectorStore:
    def __init__(self, prefix: str = "kb") -> None:
        self._prefix = prefix
        self._collections: dict[str, dict[UUID, VectorPoint]] = {}

    def collection_name(self, tenant_id: UUID) -> str:
        return f"{self._prefix}_{tenant_id.hex}"

    async def ensure_collection(self, tenant_id: UUID, *, dimension: int) -> str:
        name = self.collection_name(tenant_id)
        self._collections.setdefault(name, {})
        return name

    async def upsert(self, tenant_id: UUID, points: list[VectorPoint]) -> None:
        collection = self._collections.setdefault(self.collection_name(tenant_id), {})
        for point in points:
            collection[point.id] = point

    async def count(self, tenant_id: UUID) -> int:
        return len(self._collections.get(self.collection_name(tenant_id), {}))

    async def search_dense(
        self, tenant_id: UUID, *, vector: DenseVector, flt: SearchFilter, limit: int
    ) -> list[ScoredPoint]:
        return self._search(tenant_id, flt, limit, lambda p: _cosine(vector, p.dense))

    async def search_sparse(
        self, tenant_id: UUID, *, vector: SparseVector, flt: SearchFilter, limit: int
    ) -> list[ScoredPoint]:
        def score(p: VectorPoint) -> float:
            return _sparse_dot(vector, p.sparse) if p.sparse is not None else 0.0

        return self._search(tenant_id, flt, limit, score)

    def _search(
        self,
        tenant_id: UUID,
        flt: SearchFilter,
        limit: int,
        score_fn: Callable[[VectorPoint], float],
    ) -> list[ScoredPoint]:
        collection = self._collections.get(self.collection_name(tenant_id), {})
        scored = [
            ScoredPoint(id=p.id, score=score_fn(p), payload=p.payload)
            for p in collection.values()
            if _visible(p.payload, flt)
        ]
        scored.sort(key=lambda s: s.score, reverse=True)
        return scored[:limit]

    # Test/introspection helper (not part of the port).
    def points(self, tenant_id: UUID) -> list[VectorPoint]:
        return list(self._collections.get(self.collection_name(tenant_id), {}).values())
