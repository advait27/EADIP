"""Embedding ports: dense embedder, sparse encoder, and an embedding cache.

Dense vectors come from a versioned model (FR-016); sparse vectors carry lexical
signal for hybrid search (Phase 4). The cache keys on content hash + model
version so re-ingesting identical content is a cache hit.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Protocol

DenseVector = list[float]


@dataclass(frozen=True)
class SparseVector:
    indices: list[int]
    values: list[float]


class Embedder(Protocol):
    model_version: str
    dimension: int

    async def embed(self, texts: list[str]) -> list[DenseVector]: ...


class SparseEncoder(Protocol):
    def encode(self, texts: list[str]) -> list[SparseVector]: ...


class EmbeddingCache(Protocol):
    async def get_many(self, keys: list[str]) -> dict[str, DenseVector]: ...

    async def put_many(self, vectors: dict[str, DenseVector]) -> None: ...


def cache_key(model_version: str, text: str) -> str:
    """Stable key for the embedding cache (content + model version)."""
    digest = hashlib.sha256(f"{model_version}\x00{text}".encode()).hexdigest()
    return f"emb:{model_version}:{digest}"
