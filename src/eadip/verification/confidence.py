"""Calibrated confidence (FR-042) — a transparent rubric, not a raw model logprob.

Confidence is a function of the *verification outcome*, how many independent
sources corroborate the claim, and whether the claim is association-only
(correlation, not causation, which caps confidence for decision use).
"""

from __future__ import annotations

from eadip.verification.models import VerificationStatus

_BASE = {
    VerificationStatus.VERIFIED: 0.70,  # independently re-derived and matched
    VerificationStatus.UNVERIFIED: 0.40,  # could not be re-derived
    VerificationStatus.CONFLICTING: 0.15,  # re-derivation disagreed
}


def score(
    status: VerificationStatus, *, corroborating_sources: int, association_only: bool
) -> float:
    confidence = _BASE[status]
    confidence += min(corroborating_sources, 2) * 0.10  # up to +0.20 for corroboration
    if association_only:
        confidence -= 0.20  # association, not causation
    return round(max(0.0, min(1.0, confidence)), 2)
