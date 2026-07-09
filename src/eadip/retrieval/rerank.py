"""Reranking (FR-014): re-score the fused candidate set against the query.

The lexical reranker (token-overlap) is the deterministic default; a
cross-encoder reranker is the production seam (lazy, heavier model).
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Protocol

from eadip.retrieval.models import Passage
from eadip.retrieval.text import jaccard, tokens


class Reranker(Protocol):
    def rerank(self, query: str, passages: list[Passage]) -> list[Passage]: ...


class LexicalReranker:
    def rerank(self, query: str, passages: list[Passage]) -> list[Passage]:
        q = set(tokens(query))
        rescored = [replace(p, score=jaccard(q, set(tokens(p.text)))) for p in passages]
        rescored.sort(key=lambda p: p.score, reverse=True)
        return rescored


class CrossEncoderReranker:
    """Production cross-encoder (lazy import). Requires a sentence-transformers
    cross-encoder model; falls back is the caller's choice."""

    def __init__(self, model_name: str = "cross-encoder/ms-marco-MiniLM-L-6-v2") -> None:
        self._model_name = model_name
        self._model: Any = None

    def rerank(self, query: str, passages: list[Passage]) -> list[Passage]:
        from sentence_transformers import CrossEncoder

        if self._model is None:
            self._model = CrossEncoder(self._model_name)
        scores = self._model.predict([(query, p.text) for p in passages])
        rescored = [replace(p, score=float(s)) for p, s in zip(passages, scores, strict=True)]
        rescored.sort(key=lambda p: p.score, reverse=True)
        return rescored
