# Plan: Glass Box (feat/glass-box) — DONE (all 7 tasks landed; see ADR-0014)

Design: docs/designs/glass-box.md. Every task lands with tests; every existing gate stays green.

## Task 1 — Event log port + in-memory adapter
- `src/eadip/ports/events.py`: `LoggedEvent(seq:int, at:float, type:str, data:dict)`; `EventLog` Protocol:
  `append(run_id, event) -> LoggedEvent`, `read(run_id, after_seq=0) -> list[LoggedEvent]`,
  `subscribe(run_id) -> AsyncIterator[LoggedEvent]` (context-managed queue), `last_seq(run_id)`.
- `src/eadip/adapters/memory_event_log.py`: dict of lists + per-run subscriber queues; `stream(run_id, after_seq)`
  subscribes first, then replays from the list, then drains the queue skipping seq <= replayed.
- Tests: `tests/unit/test_event_log.py` (append seq monotonic, replay after_seq, live subscriber sees new
  events, replay+tail has no gap and no duplicate under an interleaved append).

## Task 2 — RunExecutor
- `src/eadip/orchestrator/executor.py`: `RunExecutor(orchestrator, checkpointer, event_log, gate, on_event=[])`.
  `start(state) -> bool` (one task per run id; refused if already running); `resume(tenant_id, run_id)`;
  `is_running(run_id)`; `wait(run_id)`; `shutdown()`. The task iterates `orchestrator.stream(state)`, appends each
  event, awaits every `on_event` hook, releases the gate in `finally`; on exception appends
  `run.failed {error}` and marks the checkpoint FAILED. Terminal event types: `run.done`, `run.failed`,
  `approval.required`.
- `src/eadip/gateway/dependencies.py`: `get_event_log()`, `get_run_executor()` (lru_cache singletons);
  executor hooks = notifications fan-out + budget charge (moved verbatim from the SSE route).
- `src/eadip/gateway/routes/runs.py`: POST starts the executor (429 from the concurrency gate moves here);
  GET /events = replay from `Last-Event-ID` then tail while running, emitting `id`, `event`, `data`; ends on a
  terminal event or when the executor is not running the run. `POST .../approvals/{id}` calls `executor.resume`.
- `tests/conftest.py`: `with TestClient(create_app()) as client` so one event loop lives across requests.
- Tests: `tests/unit/test_run_executor.py` (start/refuse double start/failure path/hooks called), and update
  `test_gateway.py`, `test_runs_orchestration.py`, `test_approval_route.py` expectations (Last-Event-ID resume,
  stream re-open after done returns full replay). `src/eadip/eval/load.py` unchanged (it drives the SSE).

## Task 3 — Timeline, evidence graph, evidence bundle, share
- `src/eadip/orchestrator/graph.py`: `build_evidence_graph(state) -> EvidenceGraph{nodes, edges}` (Pydantic).
  Node ids are stable (`goal`, `step:<id>`, `finding:<n>`, `claim:<n>`, `evidence:<hash>`, `rec:<n>`).
- `src/eadip/verification/bundle.py`: `build_evidence_bundle(state, warehouse, row_cap)`: per analytics claim
  with SQL provenance → `{claim_index, sql, tables, claimed, recomputed, kind, detail}`; per referenced table →
  `{name, columns, rows, truncated}` via `warehouse.execute(tenant, "SELECT cols FROM t WHERE tenant_id = '…'")`
  through `SqlSafetyValidator` first. Tables are parsed from the SQL with sqlglot.
- Routes in `runs.py`: `GET /{id}/timeline`, `GET /{id}/graph`, `GET /{id}/evidence-bundle` (all `run/runs/read`,
  audited). `src/eadip/gateway/routes/share.py`: `POST /v1/runs/{id}/share` → `{token, url, expires_at}`
  (analyst, audited `run.share.create`); `GET /v1/share/{token}` → `{run_id, question, status, timeline, graph,
  brief, bundle}` (no identity; token verified with `ServiceTokenIssuer` using the service JWT key; audited
  `run.share.view`; 404 on bad/expired). Settings: `share_link_ttl_s=604800`, `evidence_bundle_row_cap=5000`.
- Tests: `test_evidence_graph.py`, `test_evidence_bundle.py`, `test_share_route.py`, timeline/graph route tests.

## Task 4 — Real LLM path
- `tests/fakes.py`: `FakeModelClient(responses: list[str] | callable)` recording prompts.
- `src/eadip/agents/llm.py`: `complete_json` accepts `validator: Callable[[dict], T] | None`; on validation
  failure retries once with the error appended to the prompt; returns None after that.
- Tests: `test_llm_agents.py` covering complete_json (fenced, garbage, exception, repair), LLMPlanner,
  LLMGoalInterpreter, LLMReflection, LLMSqlGenerator (strip + feedback), LLMQueryExpander, LLMRecommender,
  and `LiteLLMClient.complete` with `litellm.acompletion` monkeypatched.
- Factories: analytics/verification/retrieval use `build_routing_policy(settings).route(TaskKind.X).model`.

## Task 5 — Frontend (`web/`)
- Vite + React 19 + TS; deps: react-router, d3-force, framer-motion, @duckdb/duckdb-wasm; dev deps: vitest,
  @testing-library/react, typescript. Vite proxy `/v1` → :8000. Build outDir `../src/eadip/gateway/static/app`.
- Pages: Ask, Run (graph + timeline strip + brief + verify panel + approvals), Share (replay with scrubber),
  Datasets. `lib/sse.ts` (EventSource with Last-Event-ID), `lib/graph.ts` (reducer events → graph),
  `lib/duck.ts` (lazy DuckDB-WASM, load bundle tables, run SQL, compare).
- Backend: `app.py` mounts `/app` (StaticFiles + SPA fallback) when the build dir exists; `/` → `/app` else
  `/docs`. `pyproject` includes `src/eadip/gateway/static/**` in the wheel; `.gitignore` ignores the build.
- Makefile `web`, `web-dev`, `web-test`; Dockerfile node stage; CI job `web` (npm ci, tsc, vitest, build).
- Tests: vitest for the graph reducer and the verify comparison; Python test that `/app` serves index when built.

## Task 6 — Datasets (CSV)
- `src/eadip/ingestion/datasets.py`: `infer_schema(rows)`, `parse_csv(bytes, max_bytes, max_columns)`,
  `DatasetRegistry` port + in-memory; `DuckDBWarehouse.register_table(tenant_id, table, rows)` + per-tenant
  schema union. `AnalyticsService._interpret` gains table/metric/dimension/period inference from the schema.
- Routes: `POST /v1/datasets` (multipart, `knowledge/*/read` + analyst), `GET /v1/datasets`.
- Tests: inference edge cases (empty, header only, mixed types, >max columns), analytics over an uploaded
  table end-to-end, safety gate still rejects unknown tables.

## Task 7 — Docs
- `docs/adr/0014-glass-box.md`, README "Glass Box" section + screenshots placeholder, `.env.example` keys.
