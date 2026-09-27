"""Executive report generator (FR-044, EXP-01..06): a layered brief —
headline → key findings (with confidence + provenance + verification status) →
recommendations → assumptions/limitations → raw drill-down. Conflicting and
unverified claims are surfaced, never hidden.
"""

from __future__ import annotations

from eadip.verification.models import (
    NOTE_NO_MAGNITUDE,
    NOTE_NOT_RECOMPUTED,
    ExecutiveBrief,
    Recommendation,
    VerificationReport,
    VerificationStatus,
)


def build_executive_brief(
    question: str,
    report: VerificationReport,
    recommendations: list[Recommendation],
    *,
    stop_reason: str | None = None,
) -> ExecutiveBrief:
    verified = [c for c in report.claims if c.status == VerificationStatus.VERIFIED]
    headline_claim = next(
        (
            c
            for c in report.claims
            if c.kind == "headline" and c.status == VerificationStatus.VERIFIED
        ),
        None,
    )
    headline = (
        headline_claim.claim
        if headline_claim
        else (verified[0].claim if verified else "No verified conclusion could be produced.")
    )
    overall = round(sum(c.confidence for c in verified) / len(verified), 2) if verified else 0.0

    # Exec layer: analytics + governed-tool claims ranked by confidence (raw
    # grounding passages stay in drill-down; tool results are external data the
    # brief should surface). Analytics is preferred on ties (it is recomputed).
    _source_rank = {"analytics": 1, "tool": 0}
    key_findings = sorted(
        (c for c in report.claims if c.source in ("analytics", "tool")),
        key=lambda c: (c.confidence, _source_rank.get(c.source, 0)),
        reverse=True,
    )[:6]

    assumptions = [
        "Verification re-ran each figure's recorded query on the same warehouse and "
        "recomputed the number from the fresh rows. This checks reproducibility and "
        "arithmetic, not whether the query answers the question.",
    ]
    if any(c.source in ("retrieval", "tool") for c in report.claims):
        assumptions.append(
            "Retrieval and tool claims are grounded by their source pointer; their "
            "content is not re-derived."
        )
    if any(c.association_only for c in report.claims):
        assumptions.append("Correlations are reported as associations, not causal relationships.")

    limitations: list[str] = []
    conflicting = [c for c in report.claims if c.status == VerificationStatus.CONFLICTING]
    unverified = [
        c
        for c in report.claims
        if c.status == VerificationStatus.UNVERIFIED and c.source == "analytics"
    ]
    # Reproduced but not value-checked: the query re-ran, but no comparison was made.
    reproduced_only = [c for c in unverified if c.note in (NOTE_NOT_RECOMPUTED, NOTE_NO_MAGNITUDE)]
    if conflicting:
        limitations.append(
            f"{len(conflicting)} claim(s) disagreed on re-derivation and are flagged conflicting "
            "(not reconciled)."
        )
    if unverified:
        limitations.append(
            f"{len(unverified)} analytics claim(s) are unverified: their number was not "
            "checked against a recomputed value."
        )
    if reproduced_only:
        limitations.append(
            f"{len(reproduced_only)} of those re-ran successfully, but the number could not be "
            "recomputed from the result or the claim had no number to compare; "
            "reproducing a query does not confirm its figure."
        )
    for c in report.claims:
        if c.association_only and c.status == VerificationStatus.VERIFIED:
            limitations.append(f"Association, not causation: {c.claim}")
    if any(c.source == "tool" for c in report.claims):
        limitations.append(
            "External tool results are treated as untrusted data with recorded "
            "provenance; they are not independently re-derived."
        )
    if stop_reason:
        limitations.append(
            f"The investigation stopped early ({stop_reason}); findings may be partial."
        )

    queries = sorted(
        {ref for c in report.claims if c.source == "analytics" for ref in c.provenance}
    )
    drill_down = {
        "claims": [c.model_dump(mode="json") for c in report.claims],
        "queries": queries,
    }
    return ExecutiveBrief(
        question=question,
        headline=headline,
        overall_confidence=overall,
        key_findings=key_findings,
        recommendations=recommendations,
        assumptions=assumptions,
        limitations=limitations,
        drill_down=drill_down,
    )
