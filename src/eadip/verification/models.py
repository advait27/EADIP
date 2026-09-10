"""Verification / recommendation / reporting value objects (TAD Ch 8.3, EXP-01..06).

Pydantic so a verified brief serialises into the run checkpoint and over SSE.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class VerificationStatus(StrEnum):
    VERIFIED = "verified"
    UNVERIFIED = "unverified"
    CONFLICTING = "conflicting"  # re-derivation disagrees — never silently reconciled


class VerifiedClaim(BaseModel):
    claim: str
    source: str  # "analytics" | "retrieval" | "tool"
    kind: str = ""
    status: VerificationStatus
    method: str  # "recompute" | "requery" | "source_extract" | "tool_provenance" | "none"
    confidence: float  # calibrated rubric, not a raw logprob
    association_only: bool = False
    claimed_magnitude: float | None = None
    recomputed_magnitude: float | None = None
    corroborating_sources: int = 0
    provenance: list[str] = Field(default_factory=list)  # SQL / source_ref pointers
    note: str = ""
    # The finding's re-derivation recipe (Glass Box): lets a reader recompute the
    # number from the source rows without joining back to the run's findings.
    detail: dict = Field(default_factory=dict)


class VerificationReport(BaseModel):
    claims: list[VerifiedClaim] = Field(default_factory=list)
    verified: int = 0
    unverified: int = 0
    conflicting: int = 0

    @classmethod
    def from_claims(cls, claims: list[VerifiedClaim]) -> VerificationReport:
        return cls(
            claims=claims,
            verified=sum(c.status == VerificationStatus.VERIFIED for c in claims),
            unverified=sum(c.status == VerificationStatus.UNVERIFIED for c in claims),
            conflicting=sum(c.status == VerificationStatus.CONFLICTING for c in claims),
        )


class Recommendation(BaseModel):
    action: str
    rationale: str
    impact: float  # magnitude the action addresses
    confidence: float
    based_on: list[str] = Field(default_factory=list)  # claim(s) this rests on


class ExecutiveBrief(BaseModel):
    """Layered output (EXP-01..06): headline → findings → recommendations →
    assumptions/limitations → raw drill-down."""

    question: str
    headline: str
    overall_confidence: float
    key_findings: list[VerifiedClaim] = Field(default_factory=list)
    recommendations: list[Recommendation] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    drill_down: dict = Field(default_factory=dict)  # {"claims": [...], "queries": [...]}
