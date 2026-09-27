# 14. Glass Box — see the investigation, verify it yourself, share the replay

Date: 2026-09-11

## Status
Accepted

## Context
Phases 1–12 delivered a governed investigation engine whose whole thesis is
"never take the AI's word for it", yet the only way to see it work was curl and
a raw SSE stream, every AI seam defaulted to keyword heuristics with zero test
coverage, and a run only advanced while a client held `GET /events` open. A
reader had to take the *server's* word that verification happened. Design:
`docs/designs/glass-box.md` (two adversarial review rounds; the load-bearing
findings are recorded there).

## Decisions
1. **Runs execute in the background; the stream is a subscriber.** `POST
   /v1/runs` starts a `RunExecutor` task that records every engine event in a
   durable `EventLog` (per-run, 1-based, monotonic `seq`). `GET /events` replays
   from `Last-Event-ID` and tails live events until the next terminal event
   (`run.done`, `run.failed`, `approval.required`); a finished or paused run
   replays and closes. The executor seals every log with a terminal event (a
   synthetic `run.failed` on an engine fault), releases the per-tenant
   concurrency gate in `finally`, and reports "not running" the instant a
   terminal event is recorded so no subscriber can wait on a hook. Notifications
   and budget charging moved from the SSE route into executor hooks. The 429
   from the concurrency gate moved to `POST`. Approval decisions resume through
   the executor; `resume_url` stays for compatibility.
2. **A cold-start race was fixed on the way.** FastAPI runs *sync* dependencies
   in a threadpool; three concurrent first requests each missed the
   `lru_cache` and built their own executor (two of three streams then saw an
   "unknown" run and replayed five events). Stateful singletons are resolved
   through async dependencies and warmed in the lifespan; a regression test
   drives three concurrent runs over ASGI with no lifespan, like the load
   harness.
3. **The reasoning is a graph, computed purely.** `build_evidence_graph(state)`
   yields stable node ids (`goal`, `step:<id>`, `finding:<n>`, `claim:<n>`,
   `evidence:<sha1[:12]>`, `rec:<n>`) and typed relations (`asks`, `plans`,
   `produces`, `cites`, `verifies`, `supports`). The UI folds SSE events into
   the same shape live and swaps in the authoritative graph on completion,
   matching nodes by content so positions do not jump.
4. **Verification moves to the reader.** `GET /evidence-bundle` returns each
   analytics claim's provenance SQL (re-validated by the safety gate) plus the
   tenant's rows for every table it reads, fetched by the template generator +
   validator + warehouse — the same governed read path as the verifier — with
   `group_by=False` so duplicate rows survive and client-side SUMs agree. The
   browser loads the rows into DuckDB-WASM (served locally, precompressed:
   36 MB → 6.4 MB brotli), runs each claim's SQL, and re-derives the magnitude
   with a TypeScript port of `recompute.py` using the server's tolerance.
   Findings now carry `period_column`/`dimension` in `detail`; both recomputes
   prefer the declared period column — the cardinality heuristic mis-picked
   the time axis on an uploaded table where dimension and period tied at two
   distinct values, marking a correct headline *conflicting*.
5. **One link replays a run.** `POST /share` mints an HS256 token
   `{scope: replay, run, tenant, jti, exp}` (7 days) with the service-JWT key;
   `GET /v1/share/{token}` needs no identity, refuses anything but that exact
   scope (a plain service JWT signed with the same key is a 404), applies the
   residency rule for the token's tenant, and is audited as `share:<jti>`. The
   bundle is served lazily on a sibling route. Revocation before expiry is a
   follow-up (jti deny-list).
6. **The AI path is real and tested.** `complete_json(validate=)` runs one
   generate→validate→repair round; planner, interpreter and reflection adopt
   it; `LLMRecommender` now calls the model and hard-grounds every action on a
   verified, non-association claim (ungrounded actions are dropped, confidence
   clamped to the cited claim); analytics/retrieval/verification factories
   route models through the tier policy. A `FakeModelClient` exercises every
   seam without a key.
7. **Bring your own CSV, DuckDB-only.** Headers become bare identifiers
   (reserved words `_col`, unusable → `col_<n>`, dupes `_2`, `tenant_id`
   refused); the period column is forced to text; a per-tenant physical table
   `ds_<tenant8>_<slug>` with an injected `tenant_id` joins the tenant's schema
   allowlist; provenance SQL keeps the *logical* name and the warehouse rewrites
   it at execution. The validator's column check is per-table when one table is
   referenced. The registry doubles as a tenant-scoped **vocabulary** the goal
   interpreter consults, so a question naming a dataset plans an analytics step.
   `_interpret` resolves metric/dimension/period/filter from the table's own
   schema and profiled values; a dataset with no dimension still measures the
   movement. Orphan `ds_*` tables are dropped at connect. Postgres → 501.
8. **One image ships both.** The Vite build lands in
   `src/eadip/gateway/static/app` (git-ignored; a wheel artifact); the gateway
   mounts `/app` with SPA fallback and serves `.br`/`.gz` siblings by
   `Accept-Encoding`; `/` lands on the UI when built, else `/docs`. CI gained a
   `web` job; the Dockerfile a node stage.

## Consequences
- 133 new tests (event log, executor, stream semantics incl. the cold-start
  regression, graph, bundle, share, LLM seams, static hosting, datasets) plus
  15 vitest tests; every existing gate stays green.
- The in-memory event log and dataset registry are process memory; the
  Postgres adapters are the production follow-up (ports exist).
- The demo path is unchanged for existing clients: same event names and
  payloads, plus `id:` on every SSE message.

## Amendment (2026-09-27): what the browser re-check establishes

Item 4 re-runs each claim's SQL over rows the **server supplies** and repeats
the server's arithmetic; it is not an independent verification of the data.
Each bundle table now carries a SHA-256 of its rows, and the verifier records a
commitment to the same rows at verification time, so the browser flags rows
that changed between verification and sharing (verdict `tampered`). Because the
commitment comes from the same server, it does not protect against a server
that lies consistently at verification time; that requires the commitment to be
published through a channel the server does not control.
