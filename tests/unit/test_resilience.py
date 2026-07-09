"""Resilience primitives: retry+backoff, per-step timeout, loop detection."""

from __future__ import annotations

import asyncio

import pytest

from eadip.orchestrator.models import Plan, PlanStep
from eadip.orchestrator.resilience import detect_loop, plan_similarity, with_retry, with_timeout


async def test_with_retry_succeeds_after_transient_failures() -> None:
    calls = {"n": 0}
    retries: list[int] = []

    async def factory() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("transient")
        return "ok"

    result = await with_retry(
        factory, attempts=3, base_delay=0.0, on_retry=lambda n, _e: retries.append(n)
    )
    assert result == "ok"
    assert calls["n"] == 3
    assert retries == [1, 2]


async def test_with_retry_reraises_after_exhaustion() -> None:
    async def factory() -> str:
        raise ValueError("always")

    with pytest.raises(ValueError, match="always"):
        await with_retry(factory, attempts=2, base_delay=0.0)


async def test_with_timeout_raises_on_slow_step() -> None:
    async def slow() -> None:
        await asyncio.sleep(1.0)

    with pytest.raises(asyncio.TimeoutError):
        await with_timeout(slow(), timeout_s=0.01)


def test_loop_detection() -> None:
    plan = Plan(steps=[PlanStep(id="a", kind="retrieve", description="get context for margin")])
    sig = plan.signature()
    assert plan_similarity(plan, sig) == 1.0
    assert detect_loop([sig], sig, threshold=0.92) is True
    assert detect_loop([sig], "completely unrelated tokens here", threshold=0.92) is False
