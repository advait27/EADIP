"""Sentence-aware text chunking with overlap (FR-015).

Greedy packing of sentences up to `max_chars`, carrying a trailing `overlap`
window into the next chunk so context isn't lost at boundaries. Every emitted
chunk is guaranteed <= max_chars.
"""

from __future__ import annotations

import re

from eadip.ingestion.models import Chunk

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_SPLIT.split(text) if s.strip()]


def _hard_split(sentence: str, limit: int) -> list[str]:
    """Break an over-long sentence on word (then character) boundaries."""
    out: list[str] = []
    cur = ""
    for word in sentence.split():
        if len(word) > limit:
            if cur:
                out.append(cur)
                cur = ""
            for i in range(0, len(word), limit):
                out.append(word[i : i + limit])
            continue
        candidate = f"{cur} {word}".strip()
        if len(candidate) > limit:
            out.append(cur)
            cur = word
        else:
            cur = candidate
    if cur:
        out.append(cur)
    return out


def _prepare(text: str, max_chars: int) -> list[str]:
    prepared: list[str] = []
    for sentence in _sentences(text):
        if len(sentence) <= max_chars:
            prepared.append(sentence)
        else:
            prepared.extend(_hard_split(sentence, max_chars))
    return prepared


def chunk_text(text: str, *, max_chars: int = 1200, overlap: int = 150) -> list[Chunk]:
    sentences = _prepare(text, max_chars)
    if not sentences:
        return []

    def joined(parts: list[str]) -> str:
        return " ".join(parts)

    chunks: list[str] = []
    current: list[str] = []
    for sentence in sentences:
        if current and len(joined(current)) + 1 + len(sentence) > max_chars:
            chunks.append(joined(current))
            tail: list[str] = []
            for prev in reversed(current):
                if len(joined([prev, *tail])) > overlap:
                    break
                tail.insert(0, prev)
            while tail and len(joined(tail)) + 1 + len(sentence) > max_chars:
                tail.pop(0)
            current = [*tail, sentence]
        else:
            current.append(sentence)
    if current:
        chunks.append(joined(current))

    return [Chunk(ordinal=i, text=c) for i, c in enumerate(chunks)]
