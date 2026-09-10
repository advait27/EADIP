"""Analytics value objects (TAD Ch 8.1/8.2)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from eadip.analytics.statistics import Driver


@dataclass(frozen=True)
class AnalyticsRequest:
    """A metric-movement question, plus optional structured hints. When hints are
    absent the service interprets them heuristically from ``question``/schema."""

    tenant_id: UUID
    question: str
    table: str | None = None  # an uploaded dataset's logical name (Glass Box)
    metric: str | None = None  # logical metric name (e.g. "gross_margin")
    dimension: str | None = None  # driver dimension (e.g. "product_line")
    filter_column: str = "region"  # equality-filter column for `filter_value`
    filter_value: str | None = None  # e.g. "EMEA"
    period_column: str = "period"
    baseline_period: str | None = None
    current_period: str | None = None
    effort: str = "standard"  # "simple" | "standard" | "deep"


@dataclass(frozen=True)
class QueryPlan:
    """A safe, structured intermediate representation of a query. The template
    generator renders it to SQL; the SQL is still run through the safety gate
    (defence in depth — the IR is never trusted to be safe on its own)."""

    purpose: str  # "drivers" | "trend" | "correlation" | "periods"
    table: str
    measures: tuple[tuple[str, str], ...] = ()  # (expr, alias)
    dimensions: tuple[str, ...] = ()
    distinct: bool = False
    filters: tuple[tuple[str, str], ...] = ()  # (column, string-literal value)
    period_in: tuple[str, tuple[str, ...]] | None = None  # (column, allowed values)
    order_by: tuple[tuple[str, str], ...] = ()  # (column, "ASC" | "DESC")
    limit: int | None = None
    # False renders a plain row projection (no GROUP BY) — used for the evidence
    # bundle, where duplicate source rows must survive so client-side SUMs agree.
    group_by: bool = True


@dataclass(frozen=True)
class SqlArtifact:
    """The exact query behind a set of numbers — the provenance seed (FR-024)
    that Phase 7's verifier re-executes from a fresh context."""

    purpose: str
    sql: str
    row_count: int
    truncated: bool = False
    attempts: int = 1  # how many generations it took (>1 => repaired)


@dataclass(frozen=True)
class Finding:
    kind: str  # "driver" | "anomaly" | "forecast" | "correlation"
    statement: str
    magnitude: float | None
    evidence_sql: str  # provenance: the query this claim was computed from
    detail: dict[str, Any] = field(default_factory=dict)
    association_only: bool = False  # True for correlation (not causation, FR-023)


@dataclass(frozen=True)
class AnalysisResult:
    question: str
    metric: str
    headline: str
    verified: bool  # a metric-movement explanation was actually produced
    drivers: list[Driver] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    queries: list[SqlArtifact] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    total: float | None = None  # the headline movement (sum of driver deltas, or the total)
    # Column roles the queries used, so the verifier (server or browser) can
    # re-derive numbers without guessing which text column is the time axis.
    period_column: str | None = None
    dimension: str | None = None
