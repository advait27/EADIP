"""In-process BI statistics (FR-022/023, TAD Ch 8.2) — dependency-free.

Turns warehouse numbers into explanations: additive driver attribution (what
moved the metric), correlation with multiple-comparison control, robust anomaly
detection, and a linear forecast with a proper prediction interval. Pure
stdlib (``math``/``statistics``) so it is exact, deterministic, and fully
unit-testable with no numerical libraries.

Correlation is reported as *association only* — the service labels it
"correlation, not causation" (FR-023).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from itertools import combinations


# --- driver attribution (FR-022) ---------------------------------------------
@dataclass(frozen=True)
class Driver:
    label: str
    baseline: float
    current: float
    delta: float
    share: float  # signed fraction of the total change this member explains


def attribute_change(
    baseline: dict[str, float], current: dict[str, float]
) -> tuple[float, list[Driver]]:
    """Decompose the change in an additive metric across dimension members.

    Returns ``(total_delta, drivers)`` with drivers ranked by the magnitude of
    their contribution. ``share`` sums (over members) to 1.0 when total != 0.
    """
    members = sorted(set(baseline) | set(current))
    total = sum(current.get(m, 0.0) for m in members) - sum(baseline.get(m, 0.0) for m in members)
    drivers: list[Driver] = []
    for m in members:
        b, c = baseline.get(m, 0.0), current.get(m, 0.0)
        d = c - b
        drivers.append(Driver(m, b, c, d, (d / total) if total else 0.0))
    drivers.sort(key=lambda x: abs(x.delta), reverse=True)
    return total, drivers


# --- regularised incomplete beta (for Student-t p-values) ---------------------
def _betacf(a: float, b: float, x: float) -> float:
    # Lentz's continued fraction (Numerical Recipes), good to ~1e-12.
    tiny = 1e-30
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < tiny:
        d = tiny
    d = 1.0 / d
    h = d
    for m in range(1, 200):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-12:
            break
    return h


def _betai(a: float, b: float, x: float) -> float:
    """Regularised incomplete beta I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lbeta = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
    front = math.exp(lbeta + a * math.log(x) + b * math.log(1.0 - x))
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def _t_two_sided_p(t: float, df: int) -> float:
    """Two-sided p-value for a Student-t statistic."""
    if df <= 0:
        return 1.0
    x = df / (df + t * t)
    return _betai(df / 2.0, 0.5, x)


def _t_cdf(t: float, df: int) -> float:
    tail = 0.5 * _t_two_sided_p(t, df)
    return 1.0 - tail if t >= 0 else tail


def t_critical(alpha: float, df: int) -> float:
    """Two-sided critical value t_{1-alpha/2, df} via bisection on the CDF."""
    if df <= 0:
        return float("inf")
    target = 1.0 - alpha / 2.0
    lo, hi = 0.0, 1000.0
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if _t_cdf(mid, df) < target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


# --- correlation with multiple-comparison control (FR-023) --------------------
@dataclass(frozen=True)
class Correlation:
    x: str
    y: str
    n: int
    r: float
    p_value: float
    significant: bool  # after family-wise / FDR control
    association_only: bool = True  # correlation != causation (FR-023)


def pearson(x: list[float], y: list[float]) -> float:
    n = len(x)
    if n != len(y) or n < 2:
        return 0.0
    mx, my = sum(x) / n, sum(y) / n
    sxy = sum((a - mx) * (b - my) for a, b in zip(x, y, strict=True))
    sxx = sum((a - mx) ** 2 for a in x)
    syy = sum((b - my) ** 2 for b in y)
    if sxx <= 0.0 or syy <= 0.0:
        return 0.0
    return sxy / math.sqrt(sxx * syy)


def _r_to_p(r: float, n: int) -> float:
    if n < 3 or abs(r) >= 1.0:
        return 0.0 if abs(r) >= 1.0 and n >= 3 else 1.0
    df = n - 2
    t = r * math.sqrt(df / (1.0 - r * r))
    return _t_two_sided_p(abs(t), df)


def correlate(
    series: dict[str, list[float]], *, alpha: float = 0.05, method: str = "bh"
) -> list[Correlation]:
    """Pairwise Pearson correlations across named series with multiple-comparison
    control (``method``: "bh" Benjamini-Hochberg FDR, or "bonferroni").
    """
    keys = sorted(series)
    raw: list[Correlation] = []
    for a, b in combinations(keys, 2):
        xs, ys = series[a], series[b]
        n = min(len(xs), len(ys))
        if n < 3:
            continue
        r = pearson(xs[:n], ys[:n])
        raw.append(Correlation(a, b, n, r, _r_to_p(r, n), significant=False))
    if not raw:
        return raw
    m = len(raw)
    if method == "bonferroni":
        return [Correlation(c.x, c.y, c.n, c.r, c.p_value, c.p_value <= alpha / m) for c in raw]
    # Benjamini-Hochberg: largest k with p_(k) <= (k/m)*alpha; reject all <= that.
    order = sorted(range(m), key=lambda i: raw[i].p_value)
    cutoff = -1
    for rank, idx in enumerate(order, start=1):
        if raw[idx].p_value <= (rank / m) * alpha:
            cutoff = rank
    sig_idx = {order[i] for i in range(cutoff)} if cutoff > 0 else set()
    return [Correlation(c.x, c.y, c.n, c.r, c.p_value, i in sig_idx) for i, c in enumerate(raw)]


# --- anomaly detection (FR-022) -----------------------------------------------
@dataclass(frozen=True)
class Anomaly:
    index: int
    value: float
    score: float  # modified (robust) z-score


def detect_anomalies(values: list[float], *, threshold: float = 3.5) -> list[Anomaly]:
    """Robust anomaly detection via the modified z-score (Iglewicz-Hoaglin):
    median + MAD, so a single large outlier does not mask itself."""
    n = len(values)
    if n < 3:
        return []
    s = sorted(values)
    median = s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0
    devs = sorted(abs(v - median) for v in values)
    mad = devs[n // 2] if n % 2 else (devs[n // 2 - 1] + devs[n // 2]) / 2.0
    out: list[Anomaly] = []
    if mad > 0:
        for i, v in enumerate(values):
            z = 0.6745 * (v - median) / mad
            if abs(z) >= threshold:
                out.append(Anomaly(i, v, z))
    return out


# --- forecast with prediction interval (FR-022) -------------------------------
@dataclass(frozen=True)
class Forecast:
    slope: float
    intercept: float
    next_value: float
    lower: float
    upper: float
    r_squared: float
    confidence: float


def linear_forecast(values: list[float], *, confidence: float = 0.95) -> Forecast | None:
    """OLS line through an evenly-spaced series + a prediction interval for the
    next point. Returns None when there are too few points to fit + bound."""
    n = len(values)
    if n < 3:
        return None
    xs = list(range(n))
    mx, my = sum(xs) / n, sum(values) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, values, strict=True))
    if sxx == 0:
        return None
    slope = sxy / sxx
    intercept = my - slope * mx
    fitted = [intercept + slope * x for x in xs]
    ss_res = sum((y - f) ** 2 for y, f in zip(values, fitted, strict=True))
    ss_tot = sum((y - my) ** 2 for y in values)
    r_squared = 1.0 - ss_res / ss_tot if ss_tot > 0 else 1.0
    x0 = n  # the next time step
    point = intercept + slope * x0
    df = n - 2
    s_err = math.sqrt(ss_res / df) if df > 0 else 0.0
    se_pred = s_err * math.sqrt(1.0 + 1.0 / n + (x0 - mx) ** 2 / sxx)
    margin = t_critical(1.0 - confidence, df) * se_pred
    return Forecast(
        slope=slope,
        intercept=intercept,
        next_value=point,
        lower=point - margin,
        upper=point + margin,
        r_squared=r_squared,
        confidence=confidence,
    )
