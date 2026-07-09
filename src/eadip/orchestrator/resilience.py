"""Resilience primitives (AP-8, NFR-05): retry+backoff, per-step timeout, and
plan-similarity loop detection. Deterministic and side-effect-free so they are
unit-testable in isolation.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from eadip.orchestrator.models import Plan
from eadip.retrieval.text import jaccard, tokens


async def with_timeout[T](coro: Awaitable[T], timeout_s: float) -> T:
    """Per-step wall-clock bound. Raises asyncio.TimeoutError on breach."""
    return await asyncio.wait_for(coro, timeout=timeout_s)


async def with_retry[T](
    factory: Callable[[], Awaitable[T]],
    *,
    attempts: int,
    base_delay: float,
    on_retry: Callable[[int, Exception], None] | None = None,
) -> T:
    """Run ``factory()`` with up to ``attempts`` retries and exponential backoff.
    ``factory`` must be re-callable (idempotent) — it produces a fresh awaitable
    each try. Re-raises the last exception when all attempts are exhausted."""
    last: Exception | None = None
    for attempt in range(attempts + 1):
        try:
            return await factory()
        except Exception as exc:  # noqa: BLE001 — retry policy decides what is fatal
            last = exc
            if attempt >= attempts:
                break
            if on_retry is not None:
                on_retry(attempt + 1, exc)
            await asyncio.sleep(base_delay * (2**attempt))
    assert last is not None
    raise last


def plan_similarity(plan: Plan, signature: str) -> float:
    return jaccard(set(tokens(plan.signature())), set(tokens(signature)))


def detect_loop(history: list[str], signature: str, threshold: float) -> bool:
    """True when a new plan signature is ~identical to one we have already run —
    the sign of an unproductive re-plan cycle (loop detection)."""
    new = set(tokens(signature))
    return any(jaccard(new, set(tokens(prev))) >= threshold for prev in history)
