"""The AI-safety suite is green (Phase 9 + 11 DoD, G5): all six bounds pass,
with zero unapproved actions. Requires the data extra (runs the real pipeline)."""

from __future__ import annotations

import importlib.util

import pytest

_DUCKDB = importlib.util.find_spec("duckdb") is not None


@pytest.mark.skipif(not _DUCKDB, reason="duckdb (data extra) not installed")
async def test_safety_suite_all_pass() -> None:
    from eadip.eval.safety import run_safety_suite

    report = await run_safety_suite()
    failed = [c.name for c in report.cases if not c.passed]
    assert report.ok, f"safety bounds failed: {failed}"
    names = {c.name for c in report.cases}
    assert names == {
        "bounded_autonomy",
        "grounding_suppression",
        "fail_safe_partial",
        "human_override",
        "injection_resistance",
        "memory_governance",
    }
