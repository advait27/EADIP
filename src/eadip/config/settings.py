"""Application settings (pydantic-settings).

Secrets are vault-managed in production (SEC-05); the dev defaults and optional
.env file exist only for local development.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="EADIP_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: str = "dev"
    service_name: str = "eadip-gateway"
    log_level: str = "INFO"
    log_json: bool = True

    # Stores (vault-managed in prod; .env for local dev only).
    postgres_dsn: SecretStr = SecretStr("postgresql://eadip:eadip@localhost:5432/eadip")
    redis_url: SecretStr = SecretStr("redis://localhost:6379/0")
    qdrant_url: str = "http://localhost:6333"
    neo4j_uri: str = "bolt://localhost:7687"
    # Off by default so dev/tests run without a database (in-memory adapters).
    database_enabled: bool = False

    # Authentication (Phase 2). "dev" trusts headers for local use; "oidc"
    # verifies a Bearer JWT against the IdP (SEC-02, FR-050).
    auth_mode: str = "dev"
    oidc_issuer: str | None = None
    oidc_audience: str | None = None
    oidc_jwks_uri: str | None = None
    oidc_roles_claim: str = "roles"
    oidc_tenant_claim: str = "tenant"

    # Short-lived service JWTs for internal/downstream calls (SEC-02).
    # The signing key is fetched from the vault by name, never stored here.
    service_jwt_secret_key: str = "service-jwt-signing-key"
    service_jwt_ttl_s: int = 300

    # Observability (AP-9).
    otel_enabled: bool = False
    otel_exporter_otlp_endpoint: str | None = None

    # Model routing (FR-053, full policy Phase 11): per-task tiering with
    # planning/verification pinned to the strong tier and budget-aware
    # degradation for everything else. `default_model` remains the fallback for
    # single-model consumers (e.g. the LiteLLM embedder).
    default_model: str = "anthropic/claude-sonnet-5"
    model_api_base: str | None = None
    model_tier_strong: str = "anthropic/claude-opus-5"
    model_tier_standard: str = "anthropic/claude-sonnet-5"
    model_tier_light: str = "anthropic/claude-haiku-4-5"
    routing_cost_strong_usd: float = 0.045  # per-call estimates for budget-aware
    routing_cost_standard_usd: float = 0.03  # degradation; tune per deployment
    routing_cost_light_usd: float = 0.004

    # Run guards (PRD §19 / TAD Appendix A.3).
    max_run_cost_usd: float = 2.50
    max_iterations: int = 6
    run_deadline_s: int = 480

    # Knowledge base / ingestion (Phase 3). Backends default to in-process so
    # dev/tests need no external services (TAD Ch 4/7).
    vector_backend: str = "memory"  # "memory" | "qdrant"
    embedding_backend: str = "hash"  # "hash" (deterministic dev) | "litellm"
    embedding_cache_backend: str = "memory"  # "memory" | "redis"
    hash_embedding_dim: int = 256
    litellm_embedding_model: str = "voyage/voyage-3"
    qdrant_collection_prefix: str = "kb"
    chunk_max_chars: int = 1200
    chunk_overlap: int = 150
    pii_redaction_enabled: bool = True  # redact PII in stored artifacts (NFR-08)

    # Retrieval (Phase 4, TAD Ch 7).
    retrieval_top_k: int = 8
    retrieval_candidate_k: int = 40
    rrf_k: int = 60
    retrieval_rerank_enabled: bool = True
    retrieval_multi_query_enabled: bool = True
    retrieval_context_char_budget: int = 4000
    reranker_backend: str = "lexical"  # "lexical" | "cross_encoder"
    query_expander_backend: str = "heuristic"  # "heuristic" | "llm"

    # Verification, recommendation & reporting (Phase 7, TAD Ch 8.3, FR-041..045,
    # EXP-01..06, AP-1/3). The verifier independently re-derives every number.
    verification_enabled: bool = True
    verification_rel_tolerance: float = 0.02  # recomputed-vs-claimed magnitude tolerance
    recommendation_backend: str = "heuristic"  # "heuristic" | "llm"
    max_recommendations: int = 5

    # Orchestration core (Phase 6, TAD Ch 5/6, FR-002..006, AP-6/7/8). The
    # interpret->plan->route->execute->reflect engine; bounds reuse the run guards
    # above (max_iterations / max_run_cost_usd / run_deadline_s).
    checkpoint_backend: str = "memory"  # "memory" | "postgres" (resume-on-crash)
    planner_backend: str = "heuristic"  # "heuristic" (deterministic) | "llm"
    orchestrator_step_timeout_s: float = 60.0  # per-step wall-clock bound
    orchestrator_retry_attempts: int = 2  # retries per step (idempotent)
    orchestrator_retry_base_delay_s: float = 0.05  # exponential backoff base
    orchestrator_loop_similarity: float = 0.92  # plan-similarity -> loop detected
    orchestrator_max_parallelism: int = 4  # safe parallel branches per batch

    # Analytics engine — NL->SQL + BI (Phase 5, TAD Ch 8.1/8.2, FR-020..024).
    # DuckDB is the in-process default (data extra); "postgres" runs validated SQL
    # on a read-only, RLS-scoped replica transaction.
    warehouse_backend: str = "duckdb"  # "duckdb" | "postgres"
    sql_generator_backend: str = "heuristic"  # "heuristic" (deterministic) | "llm"
    analytics_row_cap: int = 10000  # hard LIMIT injected around every query
    analytics_statement_timeout_s: float = 5.0  # enforced on the SQL backend
    analytics_max_join_tables: int = 4  # cost guard: bound the join fan-out
    analytics_max_repair_attempts: int = 2  # re-generate on validation/empty result
    analytics_cache_enabled: bool = True  # cache results keyed on (tenant, sql)
    analytics_correlation_alpha: float = 0.05  # family-wise alpha (multiple-comparison)

    # MCP integration & tool calling (Phase 8, TAD Ch 9, FR-030..033, AP-4/8).
    # Governed external systems onboarded as MCP servers → orchestrator executors.
    mcp_enabled: bool = True  # expose the tool executor + /v1/tools to the engine
    mcp_registry_backend: str = "memory"  # "memory" | "postgres" (RLS-scoped)
    mcp_register_demo_server: bool = True  # seed the deterministic demo server (dev)
    mcp_failure_threshold: int = 3  # consecutive failures → circuit breaker opens
    mcp_breaker_cooldown_s: float = 2.0  # base backoff; doubles per re-trip (capped)
    mcp_breaker_max_cooldown_s: float = 60.0
    mcp_degrade_latency_ms: float = 1500.0  # sustained latency → DEGRADED (de-prioritised)
    mcp_invoke_timeout_s: float = 15.0  # per-tool-call wall-clock bound
    mcp_max_fallbacks: int = 2  # alternate tools tried on denial/failure (AP-8)

    # GraphRAG & knowledge graph (Phase 10, FR-013, TAD Ch 4.4/7.2). Metric
    # lineage answers structural "why" questions by bounded traversal, merged with
    # vector hits on deep-effort retrieval.
    graph_enabled: bool = True  # merge graph lineage evidence into deep retrieval
    graph_backend: str = "memory"  # "memory" (in-process reference) | "neo4j"
    graph_seed_demo_lineage: bool = True  # seed the demo finance lineage (dev)
    graph_max_hops: int = 2  # bound on lineage traversal depth
    graph_max_passages: int = 6  # cap on linearized evidence passages
    neo4j_user: str = "neo4j"
    neo4j_password: SecretStr = SecretStr("neo4j")

    # Governed autonomy — human approval & safety (Phase 9, FR-034, SEC-07, AP-2).
    # The safety guarantee (G5): no write/high-impact action runs without explicit
    # human approval. Reads are autonomous; anything side-effecting is gated.
    approval_enabled: bool = True  # enforce the interrupt_before approval gate
    autonomy_auto_effects: tuple[str, ...] = ("read",)  # effects that may auto-run

    # Long-term memory (Phase 11, FR-045 full, TAD Ch 10). Episodic plan reuse +
    # PII-free shared semantic facts; erasure cascades per tenant.
    memory_enabled: bool = True
    memory_backend: str = "memory"  # "memory" | "postgres"
    memory_min_fact_confidence: float = 0.6  # below this, a claim is not memorised

    # Prompt management (Phase 11, FR-052, US-D2). Versioned immutable artifacts;
    # promotion/canary refuse without a recorded eval score >= the threshold.
    prompt_backend: str = "memory"  # "memory" | "postgres"
    prompt_eval_threshold: float = 0.8

    # Per-tenant budgets (Phase 11, FR-053, US-D3). An exhausted monthly cap
    # refuses NEW runs; in-flight runs stay bounded by max_run_cost_usd.
    budget_backend: str = "memory"  # "memory" | "postgres"

    # Notifications (Phase 11, FR-056): completion / approval / anomaly / budget.
    notification_channels: tuple[str, ...] = ("inbox", "log")
    notification_webhook_url: str | None = None

    # Rate limiting + backpressure (Phase 11, NFR-10/13). Per-tenant token bucket
    # on /v1/*; concurrent orchestration streams bounded per tenant. Defaults are
    # generous per-replica guards — the cross-replica limit lives in the ingress.
    rate_limit_enabled: bool = True
    rate_limit_rate_per_s: float = 50.0
    rate_limit_burst: int = 200
    max_concurrent_streams_per_tenant: int = 8

    # Glass Box: runs execute in the background and stream from a durable event
    # log. The in-memory log keeps the newest N runs (oldest terminal runs are
    # evicted); the Postgres adapter is the production path.
    event_log_max_runs: int = 500
    # Share links (Glass Box): a signed, read-only, expiring replay token.
    share_link_ttl_s: int = 7 * 24 * 3600
    # Bring-your-own CSV (Glass Box, DuckDB backend only).
    dataset_max_bytes: int = 5_000_000
    dataset_max_columns: int = 32
    dataset_max_rows: int = 100_000

    # Feature flags (decouple deploy from release). Seed for the runtime
    # FeatureFlagService — admins flip flags at runtime via the portal (FR-057).
    feature_flags: dict[str, bool] = Field(default_factory=dict)

    # Data residency (Phase 12, PRD §21). A tenant pinned to a region is only
    # served by deployments in that region (451 elsewhere, before data access).
    deployment_region: str = "us-east-1"
    residency_backend: str = "memory"  # "memory" | "postgres"

    # Data retention windows in days (Phase 12, GDPR storage limitation);
    # 0 = retain indefinitely. Audit events are archived, never swept.
    retention_run_days: int = 90
    retention_memory_days: int = 365
    retention_document_days: int = 730


@lru_cache
def get_settings() -> Settings:
    """Process-wide settings singleton."""
    return Settings()
