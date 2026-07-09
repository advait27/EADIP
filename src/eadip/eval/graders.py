"""Graders for the offline eval harness (TAD Ch 12).

Phase 1 ships a simple keyword grader as a seed; richer graders (DeepEval, Ragas,
grounding, verification) land in later phases.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class GradeResult:
    passed: bool
    score: float
    detail: str = ""


class Grader(Protocol):
    name: str

    def grade(self, *, expected: str, actual: str) -> GradeResult: ...


class KeywordGrader:
    """Passes when every expected keyword (pipe-separated) appears in the answer."""

    name = "keyword"

    def grade(self, *, expected: str, actual: str) -> GradeResult:
        keywords = [k.strip().lower() for k in expected.split("|") if k.strip()]
        if not keywords:
            return GradeResult(passed=False, score=0.0, detail="no keywords")
        hits = [k for k in keywords if k in actual.lower()]
        score = len(hits) / len(keywords)
        return GradeResult(
            passed=score == 1.0,
            score=score,
            detail=f"{len(hits)}/{len(keywords)} keywords",
        )
