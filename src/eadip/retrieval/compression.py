"""Context compression (FR-014): keep only decisive sentences within a budget.

Extractive: for each passage (in rank order), keep the sentences most relevant to
the query until the global character budget is exhausted, preserving order.
"""

from __future__ import annotations

from dataclasses import replace

from eadip.retrieval.models import Passage
from eadip.retrieval.text import jaccard, sentences, tokens


def compress_passages(passages: list[Passage], query: str, *, char_budget: int) -> list[Passage]:
    q = set(tokens(query))
    used = 0
    out: list[Passage] = []
    for passage in passages:
        if used >= char_budget:
            break
        sents = sentences(passage.text)
        ranked = sorted(sents, key=lambda s: jaccard(q, set(tokens(s))), reverse=True)
        keep: set[str] = set()
        for sentence in ranked:
            if used + len(sentence) > char_budget:
                continue
            keep.add(sentence)
            used += len(sentence)
        if keep:
            ordered = " ".join(s for s in sents if s in keep)
            out.append(replace(passage, text=ordered))
    return out
