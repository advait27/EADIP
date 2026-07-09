"""Vector-store port (write + search).

Per-tenant collections carry dense + sparse vectors and a payload used for
identity-scoped, time-aware filtering at retrieval time (AP-5). The filter is
applied inside the store (pre-ANN) so documents the user may not see never
influence ranking.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from eadip.ports.embeddings import DenseVector, SparseVector


@dataclass(frozen=True)
class VectorPoint:
    id: UUID
    dense: DenseVector
    sparse: SparseVector | None = None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SearchFilter:
    """Pre-ANN filter. `acl_tags` are the caller's allowed tags (any-match);
    a point with no acl_tags is public within the tenant. `as_of` selects the
    point-in-time truth (valid_from <= as_of < valid_to)."""

    acl_tags: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()
    as_of: datetime | None = None


@dataclass(frozen=True)
class ScoredPoint:
    id: UUID
    score: float
    payload: dict[str, Any]


class VectorStore(Protocol):
    async def ensure_collection(self, tenant_id: UUID, *, dimension: int) -> str:
        """Create the tenant's collection if absent; return its name."""
        ...

    async def upsert(self, tenant_id: UUID, points: list[VectorPoint]) -> None: ...

    async def count(self, tenant_id: UUID) -> int: ...

    async def search_dense(
        self, tenant_id: UUID, *, vector: DenseVector, flt: SearchFilter, limit: int
    ) -> list[ScoredPoint]: ...

    async def search_sparse(
        self, tenant_id: UUID, *, vector: SparseVector, flt: SearchFilter, limit: int
    ) -> list[ScoredPoint]: ...
