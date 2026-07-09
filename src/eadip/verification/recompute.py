"""Independent re-derivation of a finding's magnitude from a *freshly executed*
query result (FR-041). This is the heart of verification: the verifier re-runs
the finding's source SQL and this module recomputes the reported number via an
independent path (the Phase 5 statistics functions), so the check does not trust
any value the orchestrator collected.

Column roles are inferred from the result (the one all-numeric column is the
metric; text columns are dimensions/periods), so it works without hard-coding
the generated column names.
"""

from __future__ import annotations

from typing import Any

from eadip.analytics.statistics import detect_anomalies, linear_forecast, pearson
from eadip.orchestrator.models import Finding
from eadip.ports.warehouse import QueryResult


def _is_number(v: Any) -> bool:
    if isinstance(v, bool):
        return False
    if isinstance(v, int | float):
        return True
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False


def _numeric_col(result: QueryResult) -> int | None:
    for i in range(len(result.columns)):
        vals = [r[i] for r in result.rows]
        if vals and all(_is_number(v) for v in vals):
            return i
    return None


def _text_cols(result: QueryResult, numeric_idx: int) -> list[int]:
    return [i for i in range(len(result.columns)) if i != numeric_idx]


def _distinct(result: QueryResult, col: int) -> list[str]:
    return sorted({str(r[col]) for r in result.rows})


def _col_containing(result: QueryResult, cols: list[int], value: str) -> int | None:
    for c in cols:
        if value in {str(r[c]) for r in result.rows}:
            return c
    return None


def _series_by_period(result: QueryResult, period_col: int, metric_col: int) -> list[float]:
    ordered = sorted(result.rows, key=lambda r: str(r[period_col]))
    return [float(r[metric_col]) for r in ordered]


def _series_for_member(
    result: QueryResult,
    dim_col: int,
    period_col: int,
    metric_col: int,
    periods: list[str],
    member: str,
) -> list[float]:
    by = {
        str(r[period_col]): float(r[metric_col]) for r in result.rows if str(r[dim_col]) == member
    }
    return [by.get(p, 0.0) for p in periods]


def recompute_magnitude(finding: Finding, result: QueryResult) -> float | None:
    """Recompute the finding's magnitude from the fresh result, or None when the
    kind isn't independently recomputable (verifier falls back to reproducibility)."""
    found = _numeric_col(result)
    if found is None:
        return None
    ni: int = found  # annotated int so closures below capture a non-Optional
    tcols = _text_cols(result, ni)

    if finding.kind == "headline":
        if not tcols:
            return None
        period_col = min(tcols, key=lambda c: len(_distinct(result, c)))
        periods = _distinct(result, period_col)
        if len(periods) < 2:
            return None
        base, cur = periods[0], periods[-1]
        after = sum(float(r[ni]) for r in result.rows if str(r[period_col]) == cur)
        before = sum(float(r[ni]) for r in result.rows if str(r[period_col]) == base)
        return after - before

    if finding.kind == "driver":
        member = finding.detail.get("member")
        if member is None or len(tcols) < 2:
            return None
        dim_col = _col_containing(result, tcols, str(member))
        if dim_col is None:
            return None
        period_col = next(c for c in tcols if c != dim_col)
        periods = _distinct(result, period_col)
        if len(periods) < 2:
            return None
        base, cur = periods[0], periods[-1]
        after = sum(
            float(r[ni])
            for r in result.rows
            if str(r[dim_col]) == str(member) and str(r[period_col]) == cur
        )
        before = sum(
            float(r[ni])
            for r in result.rows
            if str(r[dim_col]) == str(member) and str(r[period_col]) == base
        )
        return after - before

    if finding.kind == "forecast":
        if len(tcols) != 1:
            return None
        fc = linear_forecast(_series_by_period(result, tcols[0], ni))
        return fc.next_value if fc else None

    if finding.kind == "anomaly":
        if len(tcols) != 1:
            return None
        period = str(finding.detail.get("period", ""))
        match = [r for r in result.rows if str(r[tcols[0]]) == period]
        return float(match[0][ni]) if match else None

    if finding.kind == "correlation":
        x, y = finding.detail.get("x"), finding.detail.get("y")
        if not x or not y or len(tcols) < 2:
            return None
        dim_col = _col_containing(result, tcols, str(x))
        if dim_col is None:
            return None
        period_col = next(c for c in tcols if c != dim_col)
        periods = _distinct(result, period_col)
        sx = _series_for_member(result, dim_col, period_col, ni, periods, str(x))
        sy = _series_for_member(result, dim_col, period_col, ni, periods, str(y))
        return pearson(sx, sy)

    return None


def anomaly_still_flagged(finding: Finding, result: QueryResult) -> bool:
    """Re-run robust anomaly detection on the fresh series and confirm the same
    period is still flagged — a conflict if it no longer is."""
    ni = _numeric_col(result)
    if ni is None:
        return False
    tcols = _text_cols(result, ni)
    if len(tcols) != 1:
        return False
    period_col = tcols[0]
    ordered = sorted(result.rows, key=lambda r: str(r[period_col]))
    series = [float(r[ni]) for r in ordered]
    period = str(finding.detail.get("period", ""))
    idx = next((i for i, r in enumerate(ordered) if str(r[period_col]) == period), None)
    if idx is None:
        return False
    return any(a.index == idx for a in detect_anomalies(series))
