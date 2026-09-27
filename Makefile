.PHONY: install dev test lint format type eval safety reliability load security retention up down build web web-dev web-test

install:        ## Install deps (incl. dev extras)
	uv sync --extra dev

dev:            ## Run the gateway with autoreload
	uv run uvicorn eadip.gateway:app --reload --port 8000

test:           ## Run the test suite
	uv run pytest -q

lint:           ## Lint with ruff
	uv run ruff check .

format:         ## Auto-format with ruff
	uv run ruff format .

type:           ## Type-check with mypy
	uv run mypy

eval:           ## Run the offline eval harness against gold-set v1
	uv run eadip-eval

safety:         ## Run the AI-safety suite (target: zero unapproved actions)
	uv run eadip-safety

reliability:    ## Run the fault-injection reliability suite (Phase 12)
	uv run eadip-reliability

load:           ## Load/perf gate: one replica's share of NFR-02 (200/cluster @ 2 replicas)
	uv run eadip-load --users 100

security:       ## SAST + dependency audit (blocking security gates, SEC-10)
	uv run bandit -c pyproject.toml -q -r src
	uv run pip-audit --skip-editable

retention:      ## Data-retention sweep (dry-run; --apply via eadip-retention)
	uv run eadip-retention

up:             ## Bring up the full local stack (gateway + stores + otel)
	docker compose up --build

down:           ## Tear down the local stack and volumes
	docker compose down -v

build:          ## Build the gateway image
	docker build -t eadip-gateway:dev .

web:            ## Build the Glass Box UI into the gateway (served at /app)
	cd web && npm ci && npm run build

web-dev:        ## Run the UI dev server (proxies /v1 to the gateway on :8000)
	cd web && npm install && npm run dev

web-test:       ## Type-check + unit-test the UI
	cd web && npm ci && npm run typecheck && npm test
