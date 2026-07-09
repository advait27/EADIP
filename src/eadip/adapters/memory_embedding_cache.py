"""In-memory embedding cache for dev/tests."""

from __future__ import annotations

from eadip.ports.embeddings import DenseVector


class InMemoryEmbeddingCache:
    def __init__(self) -> None:
        self._store: dict[str, DenseVector] = {}

    async def get_many(self, keys: list[str]) -> dict[str, DenseVector]:
        return {k: self._store[k] for k in keys if k in self._store}

    async def put_many(self, vectors: dict[str, DenseVector]) -> None:
        self._store.update(vectors)
