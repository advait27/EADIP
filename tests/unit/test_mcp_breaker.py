"""Circuit breaker + health state machine (Phase 8, FR-031, AP-8). Deterministic
via an injected clock: failures trip OPEN with backoff; a probe after cooldown
re-closes on success; latency degrades without failing."""

from __future__ import annotations

from eadip.mcp.breaker import CircuitBreaker
from eadip.mcp.models import ServerHealth, ServerState


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def test_failures_below_threshold_degrade_but_stay_invocable() -> None:
    clock = Clock()
    b = CircuitBreaker(failure_threshold=3, now=clock)
    h = ServerHealth()
    h = b.on_failure(h, error="boom")
    assert h.state is ServerState.DEGRADED
    assert b.allows(h)  # still serving


def test_threshold_trips_open_and_fails_fast() -> None:
    clock = Clock()
    b = CircuitBreaker(failure_threshold=3, base_cooldown_s=2.0, now=clock)
    h = ServerHealth()
    for _ in range(3):
        h = b.on_failure(h, error="boom")
    assert h.state is ServerState.FAILED
    assert not b.allows(h)  # open -> fail fast
    assert h.open_until == 2.0


def test_half_open_probe_after_cooldown_then_close_on_success() -> None:
    clock = Clock()
    b = CircuitBreaker(failure_threshold=3, base_cooldown_s=2.0, now=clock)
    h = ServerHealth()
    for _ in range(3):
        h = b.on_failure(h, error="boom")
    assert not b.allows(h)
    clock.t = 2.0  # cooldown elapsed -> half-open probe allowed
    assert b.allows(h)
    h = b.on_success(h, latency_ms=10.0)
    assert h.state is ServerState.HEALTHY
    assert h.consecutive_failures == 0


def test_backoff_grows_on_repeated_trips() -> None:
    clock = Clock()
    b = CircuitBreaker(failure_threshold=2, base_cooldown_s=1.0, max_cooldown_s=100.0, now=clock)
    h = ServerHealth()
    h = b.on_failure(h, error="x")
    h = b.on_failure(h, error="x")  # trip: cooldown 1.0
    assert h.open_until == 1.0
    h = b.on_failure(h, error="x")  # another over-threshold failure: cooldown 2.0
    assert h.open_until == clock.t + 2.0


def test_high_latency_degrades_without_failing() -> None:
    b = CircuitBreaker(degrade_latency_ms=1000.0)
    h = b.on_success(ServerHealth(), latency_ms=1500.0)
    assert h.state is ServerState.DEGRADED
    assert h.invocable


def test_draining_never_allows() -> None:
    b = CircuitBreaker()
    h = CircuitBreaker.draining(ServerHealth())
    assert h.state is ServerState.DRAINING
    assert not b.allows(h)
