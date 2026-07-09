# Service-Level Objectives (Phase 12 — GA gate evidence)

GA requires these SLOs **sustained for 30 days** (PRD M7). Measurement is the
OTel metrics pipeline (AP-9); the load gate (`make load`) proves the targets are
attainable per node-equivalent before rollout.

| SLO | Target | Measured by |
|---|---|---|
| Availability (gateway, /healthz) | 99.9 % monthly | uptime probe |
| Search p95 (`POST /v1/search`) | ≤ 2 s | OTel latency histogram; `eadip-load` gate |
| Analytics p95 (`POST /v1/analytics`) | ≤ 2 s | same |
| Run acceptance (`POST /v1/runs` → 202) p95 | ≤ 500 ms | same |
| Concurrency (NFR-02) | 200 concurrent users/node-equivalent at 0 % 5xx | `eadip-load --users 200` |
| Run completion (bounded) | 100 % of runs end in a terminal state (done/failed/awaiting_approval) — never hung | reliability suite; run-state metrics |
| Error budget | 0.1 %/month 5xx on /v1/* | gateway metrics |

**Burn policy:** >25 % of the monthly error budget in 24 h pages the on-call and
freezes feature deploys until burn returns under trend.

**Status:** engineering evidence green (load + reliability + eval + safety
gates). The 30-day sustained window is operational evidence collected in the
production environment before the GA sign-off — it cannot be produced from the
repository.
