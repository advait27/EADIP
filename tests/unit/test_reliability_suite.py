"""The fault-injection reliability suite is green (Phase 12 DoD, NFR-03/04):
all six scenarios pass against the real pipeline. Requires the data extra."""

from __future__ import annotations

import importlib.util

import pytest

_DUCKDB = importlib.util.find_spec("duckdb") is not None


@pytest.mark.skipif(not _DUCKDB, reason="duckdb (data extra) not installed")
async def test_reliability_suite_all_pass() -> None:
    from eadip.eval.reliability import run_reliability_suite

    report = await run_reliability_suite()
    failed = [c.name for c in report.cases if not c.passed]
    assert report.ok, f"reliability scenarios failed: {failed}"
    assert {c.name for c in report.cases} == {
        "checkpoint_resume",
        "idempotent_replay",
        "retry_recovery",
        "breaker_trip_recover",
        "graceful_degradation",
        "runaway_cost_stop",
    }
