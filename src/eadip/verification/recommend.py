"""Recommendation engine (FR-043): rank actions by impact × confidence, drawing
ONLY on verified, non-association claims — correlation is never turned into a
causal recommendation. Conflicting/unverified claims are surfaced in the brief's
limitations instead, never as actions.
"""

from __future__ import annotations

from typing import Protocol

from eadip.verification.models import Recommendation, VerificationStatus, VerifiedClaim


def _short(claim: str) -> str:
    return claim.split(" contributed")[0].strip() if " contributed" in claim else claim[:90]


class Recommender(Protocol):
    async def recommend(
        self, objective: str, claims: list[VerifiedClaim]
    ) -> list[Recommendation]: ...


class HeuristicRecommender:
    def __init__(self, max_recommendations: int = 5) -> None:
        self._max = max_recommendations

    async def recommend(self, objective: str, claims: list[VerifiedClaim]) -> list[Recommendation]:
        drivers = [
            c
            for c in claims
            if c.status == VerificationStatus.VERIFIED
            and not c.association_only
            and c.kind == "driver"
            and c.claimed_magnitude is not None
            and abs(c.claimed_magnitude) > 0
        ]
        drivers.sort(key=lambda c: abs(c.claimed_magnitude or 0.0) * c.confidence, reverse=True)
        recs: list[Recommendation] = [
            Recommendation(
                action=f"Remediate {_short(c.claim)} — the largest verified driver.",
                rationale=(
                    f"Independently re-derived (confidence {c.confidence:.0%}); "
                    f"it accounts for a movement of {c.claimed_magnitude:+.0f}."
                ),
                impact=abs(c.claimed_magnitude or 0.0),
                confidence=c.confidence,
                based_on=[c.claim],
            )
            for c in drivers[: self._max]
        ]
        if not recs:
            verified_headline = next(
                (
                    c
                    for c in claims
                    if c.kind == "headline" and c.status == VerificationStatus.VERIFIED
                ),
                None,
            )
            if verified_headline is not None:
                recs.append(
                    Recommendation(
                        action="Commission a driver-level breakdown to attribute the movement.",
                        rationale="The movement is verified but not yet attributed to a driver.",
                        impact=abs(verified_headline.claimed_magnitude or 0.0),
                        confidence=verified_headline.confidence,
                        based_on=[verified_headline.claim],
                    )
                )
        return recs


class LLMRecommender:
    def __init__(
        self, client: object, model: str | None = None, max_recommendations: int = 5
    ) -> None:
        self._client = client
        self._model = model
        self._fallback = HeuristicRecommender(max_recommendations)

    async def recommend(self, objective: str, claims: list[VerifiedClaim]) -> list[Recommendation]:
        # A model could phrase richer actions here; for now defer to the heuristic
        # so recommendations remain deterministic and grounded in verified claims.
        return await self._fallback.recommend(objective, claims)
