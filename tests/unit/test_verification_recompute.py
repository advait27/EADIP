"""Independent re-derivation of each finding kind from a fresh query result."""

from __future__ import annotations

from eadip.orchestrator.models import Finding
from eadip.ports.warehouse import query_result
from eadip.verification.recompute import anomaly_still_flagged, recompute_magnitude


def _finding(kind: str, magnitude: float, detail: dict) -> Finding:
    return Finding(claim=kind, source="analytics", kind=kind, magnitude=magnitude, detail=detail)


# 3 product lines x 2 periods, so `period` has fewer distinct values than the
# dimension (matching the real driver query shape).
_DRIVER_ROWS = query_result(
    ("product_line", "period", "gross_margin"),
    [
        ("Hardware", "2026-Q1", 400.0),
        ("Hardware", "2026-Q2", 180.0),
        ("Software", "2026-Q1", 600.0),
        ("Software", "2026-Q2", 610.0),
        ("Services", "2026-Q1", 250.0),
        ("Services", "2026-Q2", 230.0),
    ],
)
# 6-quarter EMEA margin series with a clear low outlier at 2026-Q2 (rows shuffled).
_TREND_ROWS = query_result(
    ("period", "gross_margin"),
    [
        ("2026-Q1", 1250.0),
        ("2025-Q4", 1250.0),
        ("2026-Q2", 1020.0),
        ("2025-Q2", 1270.0),
        ("2025-Q3", 1250.0),
        ("2025-Q1", 1300.0),
    ],
)


def test_recompute_headline_total() -> None:
    # total = (180+610+230) - (400+600+250) = 1020 - 1250 = -230
    got = recompute_magnitude(_finding("headline", -230.0, {"total": -230.0}), _DRIVER_ROWS)
    assert got == -230.0


def test_recompute_driver_member_delta() -> None:
    got = recompute_magnitude(_finding("driver", -220.0, {"member": "Hardware"}), _DRIVER_ROWS)
    assert got == -220.0  # 180 - 400


def test_recompute_anomaly_value_and_flag() -> None:
    f = _finding("anomaly", 1020.0, {"period": "2026-Q2", "z": -15.5})
    assert recompute_magnitude(f, _TREND_ROWS) == 1020.0
    assert anomaly_still_flagged(f, _TREND_ROWS) is True


def test_recompute_forecast_next_value() -> None:
    got = recompute_magnitude(_finding("forecast", 0.0, {}), _TREND_ROWS)
    assert got is not None  # a next-period forecast is produced from the series


def test_recompute_correlation_r() -> None:
    rows = query_result(
        ("period", "product_line", "gross_margin"),
        [
            ("2026-Q1", "A", 1.0),
            ("2026-Q2", "A", 2.0),
            ("2026-Q3", "A", 3.0),
            ("2026-Q1", "B", 2.0),
            ("2026-Q2", "B", 4.0),
            ("2026-Q3", "B", 6.0),
        ],
    )
    got = recompute_magnitude(_finding("correlation", 1.0, {"x": "A", "y": "B"}), rows)
    assert got is not None and abs(got - 1.0) < 1e-9  # perfectly correlated
