"""Rate limiting + backpressure (Phase 11, NFR-10/13, TAD Ch 10).

Two independent guards at the gateway edge:
  - `RateLimiter` — a per-key (tenant) token bucket; requests beyond the burst
    are refused with 429 + Retry-After rather than queued (shed early, AP-7).
  - `ConcurrencyGate` — bounds concurrently-open orchestration streams per
    tenant; at the cap, new streams are refused with 429 (backpressure) instead
    of piling onto the engine.

Both are clock-injected and deterministic for tests. The in-process state is
per-replica by design — the platform-level (cross-replica) limit lives in the
ingress; this guard protects each replica's own event loop.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response


@dataclass
class _Bucket:
    tokens: float
    updated_at: float


class RateLimiter:
    def __init__(
        self,
        *,
        rate_per_s: float,
        burst: int,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._rate = rate_per_s
        self._burst = float(burst)
        self._now = now
        self._buckets: dict[str, _Bucket] = {}

    def allow(self, key: str) -> tuple[bool, float]:
        """(allowed, retry_after_s). One token per request; refill at rate/s."""
        now = self._now()
        bucket = self._buckets.get(key)
        if bucket is None:
            bucket = _Bucket(tokens=self._burst, updated_at=now)
            self._buckets[key] = bucket
        bucket.tokens = min(self._burst, bucket.tokens + (now - bucket.updated_at) * self._rate)
        bucket.updated_at = now
        if bucket.tokens >= 1.0:
            bucket.tokens -= 1.0
            return True, 0.0
        return False, (1.0 - bucket.tokens) / self._rate if self._rate > 0 else 1.0


class ConcurrencyGate:
    """Bound concurrently-open streams per tenant (backpressure, not queueing)."""

    def __init__(self, max_concurrent: int) -> None:
        self._max = max_concurrent
        self._open: dict[str, int] = {}

    def try_acquire(self, key: str) -> bool:
        if self._open.get(key, 0) >= self._max:
            return False
        self._open[key] = self._open.get(key, 0) + 1
        return True

    def release(self, key: str) -> None:
        current = self._open.get(key, 0)
        if current <= 1:
            self._open.pop(key, None)
        else:
            self._open[key] = current - 1

    def open_count(self, key: str) -> int:
        return self._open.get(key, 0)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Apply the token bucket per tenant on /v1/* (health stays unthrottled).

    Keyed on the tenant header when present (dev-auth carries it; the OIDC path
    still benefits since the JWT's tenant is echoed by clients), falling back to
    the client host so unauthenticated bursts are also bounded.
    """

    def __init__(self, app: object, limiter: RateLimiter) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self._limiter = limiter

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if not request.url.path.startswith("/v1/"):
            return await call_next(request)
        key = request.headers.get("X-Tenant-Id") or (
            request.client.host if request.client else "anonymous"
        )
        allowed, retry_after = self._limiter.allow(key)
        if not allowed:
            return JSONResponse(
                status_code=429,
                content={"detail": "rate limit exceeded"},
                headers={"Retry-After": f"{max(1, round(retry_after))}"},
            )
        return await call_next(request)
