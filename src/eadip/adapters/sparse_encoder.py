"""Hashing lexical sparse encoder (sets up hybrid search in Phase 4).

Produces a sparse term-frequency vector over a fixed hashed vocabulary. Pure and
deterministic. A true BM25/IDF-weighted encoder can replace it behind the same
SparseEncoder port without touching the pipeline.
"""

from __future__ import annotations

import hashlib
import re

from eadip.ports.embeddings import SparseVector

_TOKEN = re.compile(r"\w+")


class HashingSparseEncoder:
    def __init__(self, num_buckets: int = 2**20) -> None:
        self._buckets = num_buckets

    def _encode_one(self, text: str) -> SparseVector:
        counts: dict[int, float] = {}
        for token in _TOKEN.findall(text.lower()):
            digest = hashlib.sha1(token.encode(), usedforsecurity=False).digest()
            idx = int.from_bytes(digest[:4], "big") % self._buckets
            counts[idx] = counts.get(idx, 0.0) + 1.0
        indices = sorted(counts)
        return SparseVector(indices=indices, values=[counts[i] for i in indices])

    def encode(self, texts: list[str]) -> list[SparseVector]:
        return [self._encode_one(t) for t in texts]
