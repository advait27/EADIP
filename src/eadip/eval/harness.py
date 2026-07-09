"""Offline eval harness: load a gold set, run answers, grade, report (FR-054)."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from eadip.eval.graders import Grader, KeywordGrader

GOLD_DIR = Path(__file__).parent / "gold_sets"


@dataclass
class Case:
    id: str
    question: str
    expected: str


@dataclass
class EvalReport:
    total: int
    passed: int
    mean_score: float

    @property
    def pass_rate(self) -> float:
        return self.passed / self.total if self.total else 0.0


def load_gold_set(name: str = "v1") -> list[Case]:
    path = GOLD_DIR / f"{name}.jsonl"
    cases: list[Case] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        cases.append(Case(id=row["id"], question=row["question"], expected=row["expected"]))
    return cases


async def run_eval(
    answer_fn: Callable[[str], Awaitable[str]],
    *,
    gold_set: str = "v1",
    grader: Grader | None = None,
) -> EvalReport:
    grader = grader or KeywordGrader()
    cases = load_gold_set(gold_set)
    passed = 0
    total_score = 0.0
    for case in cases:
        actual = await answer_fn(case.question)
        result = grader.grade(expected=case.expected, actual=actual)
        passed += int(result.passed)
        total_score += result.score
    n = len(cases)
    return EvalReport(total=n, passed=passed, mean_score=(total_score / n if n else 0.0))
