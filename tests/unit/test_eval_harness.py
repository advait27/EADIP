from __future__ import annotations

from eadip.eval.graders import KeywordGrader
from eadip.eval.harness import load_gold_set, run_eval


def test_gold_set_loads() -> None:
    cases = load_gold_set("v1")
    assert len(cases) >= 3
    assert all(c.question and c.expected for c in cases)


def test_keyword_grader() -> None:
    g = KeywordGrader()
    assert g.grade(expected="margin|emea", actual="EMEA margin fell").passed
    assert not g.grade(expected="margin|emea", actual="revenue grew").passed


async def test_harness_runs_with_echo() -> None:
    async def echo(q: str) -> str:
        return q

    report = await run_eval(echo)
    assert report.total >= 3
    # The echo SUT contains the question keywords, so the seed set passes fully.
    assert report.pass_rate == 1.0
