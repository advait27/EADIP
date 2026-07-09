"""FastAPI application factory for the API gateway."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from eadip.config.settings import get_settings
from eadip.gateway.middleware import RequestIdMiddleware
from eadip.gateway.routes import admin, analytics, health, runs, search, tools
from eadip.observability.logging import configure_logging, get_logger
from eadip.observability.otel import setup_telemetry
from eadip.platform.factory import build_rate_limiter, seed_default_prompts
from eadip.platform.ratelimit import RateLimitMiddleware


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Connect the DB pool only when persistence is enabled (off in dev/tests).
    if get_settings().database_enabled:
        from eadip.gateway.dependencies import get_database

        await get_database().connect()
    # Bootstrap the governed prompt artifacts (Phase 11): the shipped agent
    # instructions become version-1 ACTIVE; later changes go through the eval gate.
    from eadip.gateway.dependencies import get_prompt_registry

    await seed_default_prompts(get_prompt_registry())
    yield
    if get_settings().database_enabled:
        from eadip.gateway.dependencies import get_database

        await get_database().close()


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(level=settings.log_level, json_logs=settings.log_json)
    log = get_logger(__name__)

    app = FastAPI(
        title="EADIP Gateway",
        version="0.1.0",
        description="Enterprise Autonomous Decision Intelligence Platform — API Gateway.",
        lifespan=lifespan,
    )
    app.add_middleware(RequestIdMiddleware)
    if settings.rate_limit_enabled:
        # Outermost-added runs first: shed excess load before any work happens.
        app.add_middleware(RateLimitMiddleware, limiter=build_rate_limiter(settings))
    app.include_router(health.router)
    app.include_router(runs.router)
    app.include_router(search.router)
    app.include_router(analytics.router)
    app.include_router(tools.router)
    app.include_router(admin.router)

    setup_telemetry(
        app,
        service_name=settings.service_name,
        endpoint=settings.otel_exporter_otlp_endpoint,
        enabled=settings.otel_enabled,
    )
    log.info("gateway.startup", environment=settings.environment)
    return app


app = create_app()
