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


RECOMMENDER_INSTRUCTION = (
    "Propose concrete actions from the VERIFIED, non-association claims only. "
    'Reply JSON {"recommendations":[{"action","rationale","impact","confidence","based_on"}]} '
    "where based_on lists the exact claim texts used. Never act on a conflicting, "
    "unverified or correlation-only claim."
)


class LLMRecommender:
    """Model-phrased actions, hard-grounded: every recommendation must cite at
    least one verified non-association claim (by exact text) or it is dropped;
    impact/confidence are clamped to what the cited claims support. Any model
    failure or an empty grounded set falls back to the heuristic."""

    def __init__(
        self, client: object, model: str | None = None, max_recommendations: int = 5
    ) -> None:
        self._client = client
        self._model = model
        self._max = max_recommendations
        self._fallback = HeuristicRecommender(max_recommendations)

    async def recommend(self, objective: str, claims: list[VerifiedClaim]) -> list[Recommendation]:
        from eadip.agents.llm import complete_json

        eligible = {
            c.claim: c
            for c in claims
            if c.status == VerificationStatus.VERIFIED and not c.association_only
        }
        if not eligible:
            return await self._fallback.recommend(objective, claims)
        prompt = (
            f"{RECOMMENDER_INSTRUCTION}\nObjective: {objective}\nVerified claims: {list(eligible)}"
        )

        def validate(data: dict) -> list[Recommendation]:
            raw = data.get("recommendations")
            if not isinstance(raw, list) or not raw:
                raise ValueError("recommendations must be a non-empty list")
            out: list[Recommendation] = []
            for item in raw:
                rec = Recommendation.model_validate(item)
                cited = [c for c in rec.based_on if c in eligible]
                if not cited:
                    continue  # ungrounded -> dropped, never surfaced
                support = max(eligible[c].confidence for c in cited)
                out.append(
                    rec.model_copy(
                        update={
                            "based_on": cited,
                            "confidence": min(max(rec.confidence, 0.0), support),
                            "impact": abs(rec.impact),
                        }
                    )
                )
            if not out:
                raise ValueError("no recommendation cites a verified claim")
            return out[: self._max]

        recs = await complete_json(self._client, prompt, model=self._model, validate=validate)
        return recs if recs is not None else await self._fallback.recommend(objective, claims)
