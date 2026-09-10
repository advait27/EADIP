"""Build the analytics stack from settings (warehouse, generator, cache, service).

Heavy/engine pieces (DuckDB, the Postgres replica) are imported lazily so the
default install and the safety-gate unit tests need no analytical engine.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from eadip.analytics.cache import InMemorySqlResultCache, NullSqlResultCache, SqlResultCache
from eadip.analytics.service import AnalyticsService
from eadip.analytics.sql_generator import SqlGenerator, TemplateSqlGenerator
from eadip.config.settings import Settings
from eadip.ports.warehouse import Warehouse

if TYPE_CHECKING:
    from eadip.adapters.postgres import Database


def build_generator(settings: Settings) -> SqlGenerator:
    if settings.sql_generator_backend == "llm":
        from eadip.adapters.litellm_client import LiteLLMClient
        from eadip.analytics.sql_generator import LLMSqlGenerator
        from eadip.platform.factory import build_routing_policy
        from eadip.platform.models import TaskKind

        client = LiteLLMClient(settings.default_model, settings.model_api_base)
        model = build_routing_policy(settings).route(TaskKind.SQL_GENERATE).model
        return LLMSqlGenerator(client, model)
    return TemplateSqlGenerator()


def build_cache(settings: Settings) -> SqlResultCache:
    return InMemorySqlResultCache() if settings.analytics_cache_enabled else NullSqlResultCache()


def build_warehouse(settings: Settings, *, database: Database | None = None) -> Warehouse:
    if settings.warehouse_backend == "postgres":
        from eadip.adapters.postgres_warehouse import PostgresWarehouse

        if database is None:
            raise RuntimeError("warehouse_backend='postgres' requires a Database")
        return PostgresWarehouse(database)
    from eadip.adapters.duckdb_warehouse import DuckDBWarehouse

    return DuckDBWarehouse()


def build_analytics_service(settings: Settings, warehouse: Warehouse) -> AnalyticsService:
    return AnalyticsService(
        warehouse=warehouse,
        generator=build_generator(settings),
        cache=build_cache(settings),
        row_cap=settings.analytics_row_cap,
        statement_timeout_s=settings.analytics_statement_timeout_s,
        max_join_tables=settings.analytics_max_join_tables,
        max_repair_attempts=settings.analytics_max_repair_attempts,
        correlation_alpha=settings.analytics_correlation_alpha,
    )
