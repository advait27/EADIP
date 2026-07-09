"""Small text helpers shared across retrieval (tokenize, sentences, overlap)."""

from __future__ import annotations

import re

_TOKEN = re.compile(r"\w+")
_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")

_STOPWORDS = {
    "the",
    "a",
    "an",
    "and",
    "or",
    "but",
    "of",
    "to",
    "in",
    "on",
    "for",
    "with",
    "is",
    "are",
    "was",
    "were",
    "be",
    "been",
    "by",
    "at",
    "as",
    "it",
    "this",
    "that",
    "these",
    "those",
    "from",
    "did",
    "do",
    "does",
    "why",
    "what",
    "how",
    "which",
    "who",
    "when",
    "where",
}


def tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def content_tokens(text: str) -> list[str]:
    return [t for t in tokens(text) if t not in _STOPWORDS and len(t) > 2]


def sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE.split(text) if s.strip()]


def jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 0.0
    return len(a & b) / len(a | b)
