"""Deterministic hash embedder for dev/tests (no model call, FR-016 seed).

Maps text to a stable unit-norm vector via hashed token features. Deterministic
so tests are reproducible and the embedding cache actually hits. Production uses
LiteLLMEmbedder (or another real model) behind the same Embedder port.
"""

from __future__ import annotations

import hashlib
import math

from eadip.ports.embeddings import DenseVector


class HashEmbedder:
    def __init__(self, dimension: int = 256, model_version: str = "hash-v1") -> None:
        self.dimension = dimension
        self.model_version = model_version

    def _embed_one(self, text: str) -> DenseVector:
        vec = [0.0] * self.dimension
        for token in text.lower().split():
            digest = hashlib.sha1(token.encode(), usedforsecurity=False).digest()
            idx = int.from_bytes(digest[:4], "big") % self.dimension
            sign = 1.0 if digest[4] & 1 else -1.0
            vec[idx] += sign
        norm = math.sqrt(sum(v * v for v in vec))
        if norm == 0.0:
            return vec
        return [v / norm for v in vec]

    async def embed(self, texts: list[str]) -> list[DenseVector]:
        return [self._embed_one(t) for t in texts]
