"""Confidence rubric, recommendation ranking, and the executive brief."""

from __future__ import annotations

from eadip.verification.confidence import score
from eadip.verification.models import (
    Recommendation,
    VerificationReport,
    VerificationStatus,
    VerifiedClaim,
)
from eadip.verification.recommend import HeuristicRecommender
from eadip.verification.report import build_executive_brief


def test_confidence_rubric() -> None:
    assert (
        score(VerificationStatus.VERIFIED, corroborating_sources=0, association_only=False) == 0.70
    )
    assert (
        score(VerificationStatus.VERIFIED, corroborating_sources=2, association_only=False) == 0.90
    )
    assert (
        score(VerificationStatus.CONFLICTING, corroborating_sources=0, association_only=False)
        == 0.15
    )
    # correlation != causation caps confidence
    assert (
        score(VerificationStatus.VERIFIED, corroborating_sources=0, association_only=True) == 0.50
    )


def _driver(member: str, magnitude: float, conf: float) -> VerifiedClaim:
    return VerifiedClaim(
        claim=f"product_line '{member}' contributed {magnitude:+.0f}",
        source="analytics",
        kind="driver",
        status=VerificationStatus.VERIFIED,
        method="recompute",
        confidence=conf,
        claimed_magnitude=magnitude,
    )


async def test_recommendations_rank_by_impact_and_exclude_associations() -> None:
    claims = [
        _driver("Services", -20.0, 0.8),
        _driver("Hardware", -220.0, 0.8),
        VerifiedClaim(
            claim="A and B move together",
            source="analytics",
            kind="correlation",
            status=VerificationStatus.VERIFIED,
            method="recompute",
            confidence=0.5,
            association_only=True,
            claimed_magnitude=0.9,
        ),
    ]
    recs = await HeuristicRecommender().recommend("why did margin fall?", claims)
    assert len(recs) == 2  # the correlation (association-only) is not recommended
    assert "Hardware" in recs[0].action  # ranked by impact x confidence
    assert recs[0].impact == 220.0


async def test_executive_brief_flags_conflicts_and_associations() -> None:
    claims = [
        VerifiedClaim(
            claim="EMEA gross margin fell by 230",
            source="analytics",
            kind="headline",
            status=VerificationStatus.VERIFIED,
            method="recompute",
            confidence=0.8,
            claimed_magnitude=-230.0,
            provenance=["SELECT ... FROM finance_metrics ..."],
        ),
        VerifiedClaim(
            claim="A and B move together",
            source="analytics",
            kind="correlation",
            status=VerificationStatus.VERIFIED,
            method="recompute",
            confidence=0.5,
            association_only=True,
        ),
        VerifiedClaim(
            claim="disputed number",
            source="analytics",
            kind="driver",
            status=VerificationStatus.CONFLICTING,
            method="recompute",
            confidence=0.15,
        ),
    ]
    report = VerificationReport.from_claims(claims)
    recs = [Recommendation(action="do x", rationale="r", impact=230.0, confidence=0.8)]
    brief = build_executive_brief("Why did EMEA margin fall?", report, recs)

    assert brief.headline == "EMEA gross margin fell by 230"
    assert brief.overall_confidence > 0
    assert brief.recommendations == recs
    assert any("association" in lim.lower() for lim in brief.limitations)
    assert any("conflicting" in lim.lower() for lim in brief.limitations)
    assert "claims" in brief.drill_down and "queries" in brief.drill_down
