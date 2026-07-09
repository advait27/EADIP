"""Circuit breaker + health state machine (FR-031, AP-8, NFR-05).

Wraps each server's health. A run of failures trips the breaker OPEN (state
FAILED) for a backoff window; while open, invocations are refused instantly
(fail fast) so a sick connector can't stall the orchestrator. After the window
the breaker goes HALF-OPEN — the next call is allowed through as a probe; success
closes it (HEALTHY), failure re-opens it with a longer window. Elevated latency
without failure degrades the server (DEGRADED) — still invocable, but the Tool
Agent de-prioritises it. DRAINING is a terminal, operator-set state (no new
calls) used when a server is being removed.

Pure and clock-injected (like the orchestrator) so it is fully deterministic in
tests. Records outcomes; the registry owns the `ServerHealth` it mutates.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from eadip.mcp.models import ServerHealth, ServerState


class CircuitBreaker:
    def __init__(
        self,
        *,
        failure_threshold: int = 3,
        base_cooldown_s: float = 2.0,
        max_cooldown_s: float = 60.0,
        degrade_latency_ms: float = 1500.0,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._failure_threshold = failure_threshold
        self._base_cooldown_s = base_cooldown_s
        self._max_cooldown_s = max_cooldown_s
        self._degrade_latency_ms = degrade_latency_ms
        self._now = now

    def allows(self, health: ServerHealth) -> bool:
        """May a call go through right now? DRAINING never; FAILED only once the
        cooldown has elapsed (HALF-OPEN probe). HEALTHY/DEGRADED always."""
        if health.state is ServerState.DRAINING:
            return False
        if health.state is ServerState.FAILED:
            return self._now() >= health.open_until  # half-open: allow a probe
        return True

    def on_success(self, health: ServerHealth, *, latency_ms: float) -> ServerHealth:
        """A successful call: close the breaker. Sustained high latency degrades
        (still invocable) rather than fails."""
        degraded = latency_ms >= self._degrade_latency_ms
        return health.model_copy(
            update={
                "state": ServerState.DEGRADED if degraded else ServerState.HEALTHY,
                "consecutive_failures": 0,
                "last_latency_ms": latency_ms,
                "last_error": "",
                "open_until": 0.0,
            }
        )

    def on_failure(self, health: ServerHealth, *, error: str) -> ServerHealth:
        """A failed call: count it; trip OPEN with exponential backoff once the
        threshold is crossed."""
        failures = health.consecutive_failures + 1
        if failures >= self._failure_threshold:
            # backoff grows with each threshold-worth of failures, capped.
            over = failures - self._failure_threshold
            cooldown = min(self._base_cooldown_s * (2**over), self._max_cooldown_s)
            return health.model_copy(
                update={
                    "state": ServerState.FAILED,
                    "consecutive_failures": failures,
                    "last_error": error[:200],
                    "open_until": self._now() + cooldown,
                }
            )
        # Below threshold: mark degraded but keep serving.
        return health.model_copy(
            update={
                "state": ServerState.DEGRADED,
                "consecutive_failures": failures,
                "last_error": error[:200],
            }
        )

    @staticmethod
    def draining(health: ServerHealth) -> ServerHealth:
        return health.model_copy(update={"state": ServerState.DRAINING})
