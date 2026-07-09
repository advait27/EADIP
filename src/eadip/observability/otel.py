"""OpenTelemetry wiring (AP-9).

No-op when disabled or when the optional `otel` extra is not installed, so local
dev and unit tests never require a running collector.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from eadip.observability.logging import get_logger

if TYPE_CHECKING:
    from fastapi import FastAPI

log = get_logger(__name__)


def setup_telemetry(
    app: FastAPI,
    *,
    service_name: str,
    endpoint: str | None,
    enabled: bool,
) -> None:
    if not enabled:
        log.info("otel.disabled")
        return
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        log.warning("otel.libs_missing", hint="install the 'otel' extra")
        return

    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    if endpoint:
        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, insecure=True))
        )
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app)
    log.info("otel.enabled", endpoint=endpoint)
