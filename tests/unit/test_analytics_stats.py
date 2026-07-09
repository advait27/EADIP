"""BI statistics (FR-022/023): driver attribution, correlation + multiple-
comparison control, robust anomaly detection, and a linear forecast."""

from __future__ import annotations

from eadip.analytics.statistics import (
    attribute_change,
    correlate,
    detect_anomalies,
    linear_forecast,
    pearson,
    t_critical,
)


def test_attribute_change_ranks_drivers_and_shares_sum_to_one() -> None:
    baseline = {"Hardware": 400, "Software": 600, "Services": 250}
    current = {"Hardware": 180, "Software": 610, "Services": 230}
    total, drivers = attribute_change(baseline, current)
    assert total == -230
    assert drivers[0].label == "Hardware"  # biggest mover ranked first
    assert drivers[0].delta == -220
    assert abs(sum(d.share for d in drivers) - 1.0) < 1e-9


def test_t_critical_matches_known_table_values() -> None:
    assert abs(t_critical(0.05, 10) - 2.228) < 0.01
    assert abs(t_critical(0.05, 30) - 2.042) < 0.01


def test_pearson_extremes() -> None:
    assert abs(pearson([1, 2, 3, 4, 5], [2, 4, 6, 8, 10]) - 1.0) < 1e-9
    assert abs(pearson([1, 2, 3, 4, 5], [10, 8, 6, 4, 2]) + 1.0) < 1e-9


def test_correlation_multiple_comparison_control() -> None:
    series = {
        "a": [1, 2, 3, 4, 5, 6, 7, 8],
        "b": [2, 4, 6, 8, 10, 12, 14, 16],  # perfectly correlated with a
        "noise": [5, 1, 4, 2, 9, 3, 8, 1],
    }
    results = {(c.x, c.y): c for c in correlate(series, alpha=0.05)}
    assert results[("a", "b")].significant is True
    assert results[("a", "b")].association_only is True
    assert results[("a", "noise")].significant is False
    assert results[("b", "noise")].significant is False


def test_detect_anomalies_flags_single_outlier() -> None:
    anomalies = detect_anomalies([1300, 1270, 1250, 1250, 1250, 1020])
    assert [a.index for a in anomalies] == [5]
    assert anomalies[0].value == 1020
    assert anomalies[0].score < -3.5


def test_linear_forecast_on_perfect_line() -> None:
    fc = linear_forecast([0, 2, 4, 6, 8])
    assert fc is not None
    assert abs(fc.slope - 2.0) < 1e-9
    assert abs(fc.next_value - 10.0) < 1e-9
    assert abs(fc.r_squared - 1.0) < 1e-9
    assert fc.lower <= fc.next_value <= fc.upper


def test_forecast_needs_enough_points() -> None:
    assert linear_forecast([1.0, 2.0]) is None
