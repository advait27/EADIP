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


def test_checked_coverage_counts_only_value_compared_analytics_claims() -> None:
    from eadip.eval.run_eval import VerificationStats
    from eadip.verification.models import VerificationStatus, VerifiedClaim

    def claim(source: str, status: VerificationStatus, checked: bool) -> VerifiedClaim:
        return VerifiedClaim(
            claim="c",
            source=source,
            status=status,
            method="recompute" if source == "analytics" else "source_extract",
            confidence=0.7,
            provenance=["ref"],
            value_checked=checked,
        )

    stats = VerificationStats()
    for c in [
        claim("analytics", VerificationStatus.VERIFIED, True),
        claim("analytics", VerificationStatus.CONFLICTING, True),
        # reproduced but not recomputed: has a method, but was not value-checked
        claim("analytics", VerificationStatus.UNVERIFIED, False),
        claim("analytics", VerificationStatus.UNVERIFIED, False),
        claim("retrieval", VerificationStatus.VERIFIED, False),
        claim("tool", VerificationStatus.VERIFIED, False),
    ]:
        stats.add(c)
    assert stats.analytics_claims == 4 and stats.value_checked == 2
    assert stats.coverage == 0.5  # retrieval/tool are not in the denominator
    assert stats.pointer_grounded == 2  # reported separately
    assert stats.grounding == 1.0  # provenance check over the 3 VERIFIED claims
