"""Load/performance harness (Phase 12, NFR-02, TAD Ch 16).

Drives the REAL gateway app in-process over ASGI (no network stack, no mocks) at
N concurrent virtual users — each a distinct tenant, exercising the multi-tenant
paths (per-tenant rate buckets, stores, RLS-shaped scoping) exactly as deployed.

Workload mix per iteration (weighted like the interactive profile):
  - 70%  POST /v1/search      (hybrid retrieval read path)
  - 20%  POST /v1/analytics   (NL->SQL + BI path, DuckDB in-process)
  - 10%  POST /v1/runs + full SSE stream (the whole orchestrated pipeline)

Reports throughput, latency percentiles and error rate, and GATES on them:
non-zero exit when the error rate or p95 breaches the thresholds. NFR-02 is
**200 concurrent per cluster**; the deployment floor is 2 replicas (Helm
`replicaCount: 2`, KEDA scales 2→12), so the single-node gate defaults to the
per-replica share (100 users). Deterministic workload; wall-clock timing is
the measurement, so run on quiet hardware.

Usage: eadip-load [--users 100] [--iterations 3] [--p95 2.0] [--error-rate 0.0]
"""

from __future__ import annotations

import argparse
import asyncio
import time
from dataclasses import dataclass, field
from uuid import uuid4

import httpx


@dataclass
class LoadReport:
    users: int
    duration_s: float
    latencies_ms: dict[str, list[float]] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)

    @property
    def total_requests(self) -> int:
        return sum(len(v) for v in self.latencies_ms.values()) + len(self.failures)

    @property
    def error_rate(self) -> float:
        return len(self.failures) / self.total_requests if self.total_requests else 0.0

    @property
    def rps(self) -> float:
        return self.total_requests / self.duration_s if self.duration_s else 0.0

    def percentile(self, p: float, path: str | None = None) -> float:
        values = sorted(
            v
            for key, vals in self.latencies_ms.items()
            if path is None or key == path
            for v in vals
        )
        if not values:
            return 0.0
        idx = min(len(values) - 1, max(0, round(p / 100 * (len(values) + 1)) - 1))
        return values[idx]


async def _virtual_user(
    client: httpx.AsyncClient, user_index: int, iterations: int, report: LoadReport
) -> None:
    tenant = str(uuid4())
    headers = {"X-Roles": "analyst", "X-Tenant-Id": tenant, "X-User-Id": str(uuid4())}

    async def timed(label: str, coro_factory):  # type: ignore[no-untyped-def]
        start = time.perf_counter()
        try:
            ok = await coro_factory()
        except Exception as exc:  # noqa: BLE001 — a crash IS the finding
            report.failures.append(f"{label}: {type(exc).__name__}: {exc}")
            return
        elapsed_ms = (time.perf_counter() - start) * 1000
        if ok:
            report.latencies_ms.setdefault(label, []).append(elapsed_ms)
        else:
            report.failures.append(f"{label}: non-2xx")

    async def do_search() -> bool:
        r = await client.post(
            "/v1/search",
            headers=headers,
            json={"query": "Why did EMEA gross margin fall last quarter?", "effort": "standard"},
        )
        return r.status_code == 200

    async def do_analytics() -> bool:
        r = await client.post(
            "/v1/analytics",
            headers=headers,
            json={"question": "Why did gross margin fall in EMEA?", "metric": "gross_margin"},
        )
        return r.status_code == 200

    async def do_full_run() -> bool:
        created = await client.post(
            "/v1/runs",
            headers=headers,
            json={"question": "Why did EMEA gross margin fall last quarter?"},
        )
        if created.status_code != 202:
            return False
        run_id = created.json()["id"]
        done = False
        async with client.stream(
            "GET", f"/v1/runs/{run_id}/events", headers=headers, timeout=120.0
        ) as stream:
            async for line in stream.aiter_lines():
                if line.startswith("event: run.done"):
                    done = True
        return done

    for i in range(iterations):
        # Deterministic 70/20/10 mix, phase-shifted per user so the mix holds
        # globally at every instant.
        slot = (user_index + i) % 10
        if slot < 7:
            await timed("search", do_search)
        elif slot < 9:
            await timed("analytics", do_analytics)
        else:
            await timed("run", do_full_run)


async def run_load(users: int, iterations: int) -> LoadReport:
    # Import here so `--help` works without the app's (data-extra) dependencies.
    from eadip.gateway import create_app

    app = create_app()
    report = LoadReport(users=users, duration_s=0.0)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://load.local", timeout=60.0
    ) as client:
        start = time.perf_counter()
        await asyncio.gather(*(_virtual_user(client, i, iterations, report) for i in range(users)))
        report.duration_s = time.perf_counter() - start
    return report


def print_report(report: LoadReport) -> None:
    print(
        f"load: {report.users} concurrent users, {report.total_requests} requests "
        f"in {report.duration_s:.1f}s ({report.rps:.1f} req/s), "
        f"error rate {report.error_rate:.2%}"
    )
    for label in sorted(report.latencies_ms):
        n = len(report.latencies_ms[label])
        print(
            f"  {label:<10} n={n:<5} p50={report.percentile(50, label):>7.1f}ms "
            f"p95={report.percentile(95, label):>8.1f}ms "
            f"p99={report.percentile(99, label):>8.1f}ms"
        )
    for failure in report.failures[:5]:
        print(f"  FAIL {failure}")


def gate(report: LoadReport, *, max_p95_ms: float, max_error_rate: float) -> list[str]:
    """NFR-02 gate: which thresholds were breached (empty = pass). The p95 gate
    applies to the interactive read paths (search/analytics); a full orchestrated
    run is an accepted long-running SSE workload bounded by its own deadline."""
    breaches: list[str] = []
    if report.error_rate > max_error_rate:
        breaches.append(f"error rate {report.error_rate:.2%} > {max_error_rate:.2%}")
    for label in ("search", "analytics"):
        p95 = report.percentile(95, label)
        if p95 > max_p95_ms:
            breaches.append(f"{label} p95 {p95:.0f}ms > {max_p95_ms:.0f}ms")
    return breaches


def main() -> None:
    parser = argparse.ArgumentParser(description="EADIP load/perf gate (NFR-02)")
    parser.add_argument(
        "--users",
        type=int,
        default=100,
        help="concurrent virtual users (default: one replica's share of the "
        "200-per-cluster NFR-02 target at the 2-replica deployment floor)",
    )
    parser.add_argument("--iterations", type=int, default=3, help="requests per user")
    parser.add_argument("--p95", type=float, default=2.0, help="max p95 seconds (read paths)")
    parser.add_argument("--error-rate", type=float, default=0.0, help="max error rate (0..1)")
    args = parser.parse_args()

    report = asyncio.run(run_load(args.users, args.iterations))
    print_report(report)
    breaches = gate(report, max_p95_ms=args.p95 * 1000, max_error_rate=args.error_rate)
    if breaches:
        for b in breaches:
            print(f"  GATE BREACH: {b}")
        raise SystemExit(1)
    print(f"  GATE PASS: error rate <= {args.error_rate:.0%}, read-path p95 <= {args.p95:.1f}s")
    raise SystemExit(0)


if __name__ == "__main__":
    main()
