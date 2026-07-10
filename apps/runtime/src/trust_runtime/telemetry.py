"""OpenTelemetry configuration and correlation helpers."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from functools import wraps
from threading import Lock
from typing import ParamSpec, TypeVar

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Span, Tracer

from .config import RuntimeSettings

_configuration_lock = Lock()
_configured = False
P = ParamSpec("P")
R = TypeVar("R")


def configure_telemetry(settings: RuntimeSettings) -> None:
    """Install one SDK provider and attach OTLP export when configured."""

    global _configured
    if _configured:
        return
    with _configuration_lock:
        if _configured:
            return
        provider = TracerProvider(
            resource=Resource.create(
                {
                    "service.name": settings.otel_service_name,
                    "service.version": "0.1.0",
                    "deployment.environment": settings.app_env.value,
                }
            )
        )
        if settings.otel_exporter_otlp_endpoint:
            exporter = OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint)
            provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
        _configured = True


def tracer(component: str) -> Tracer:
    return trace.get_tracer(f"trust_runtime.{component}", "0.1.0")


def set_attributes(span: Span, attributes: Mapping[str, object]) -> None:
    for key, value in attributes.items():
        if isinstance(value, (str, bool, int, float)):
            span.set_attribute(key, value)


def current_trace_id() -> str | None:
    context = trace.get_current_span().get_span_context()
    if not context.is_valid:
        return None
    return f"{context.trace_id:032x}"


def traced(name: str, *, component: str) -> Callable[[Callable[P, R]], Callable[P, R]]:
    def decorator(function: Callable[P, R]) -> Callable[P, R]:
        @wraps(function)
        def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
            with tracer(component).start_as_current_span(name):
                return function(*args, **kwargs)

        return wrapped

    return decorator
