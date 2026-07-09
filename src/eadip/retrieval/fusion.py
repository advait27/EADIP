"""Reciprocal Rank Fusion (RRF) — combine rankings without score calibration.

Rank-based and scale-free, so dense, sparse, and multi-query result lists fuse
cleanly (TAD Ch 7.1).
"""

from __future__ import annotations

from uuid import UUID

from eadip.ports.vector_store import ScoredPoint


def reciprocal_rank_fusion(rankings: list[list[ScoredPoint]], *, k: int = 60) -> list[ScoredPoint]:
    scores: dict[UUID, float] = {}
    payloads: dict[UUID, dict] = {}
    for ranking in rankings:
        for rank, point in enumerate(ranking):
            scores[point.id] = scores.get(point.id, 0.0) + 1.0 / (k + rank + 1)
            payloads.setdefault(point.id, point.payload)
    fused = [
        ScoredPoint(id=pid, score=score, payload=payloads[pid]) for pid, score in scores.items()
    ]
    fused.sort(key=lambda p: p.score, reverse=True)
    return fused
