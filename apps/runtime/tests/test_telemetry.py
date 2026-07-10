import pytest
from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import (
    INVALID_SPAN_CONTEXT,
    NonRecordingSpan,
    SpanContext,
    TraceFlags,
    TraceState,
)
from trust_contracts import uuid7

from trust_runtime.api import create_app
from trust_runtime.config import RuntimeSettings
from trust_runtime.telemetry import current_trace_id
from trust_runtime.worker import ServiceEventSink


class EventService:
    def __init__(self) -> None:
        self.events: list[tuple[object, str, dict[str, object]]] = []

    def append_worker_event(self, run_id, event_type: str, payload: dict[str, object]) -> None:
        self.events.append((run_id, event_type, payload))


@pytest.mark.asyncio
async def test_worker_events_carry_run_and_trace_correlation() -> None:
    run_id = uuid7()
    trace_id = 0x1234567890ABCDEF1234567890ABCDEF
    context = SpanContext(
        trace_id=trace_id,
        span_id=0x1234567890ABCDEF,
        is_remote=False,
        trace_flags=TraceFlags.SAMPLED,
        trace_state=TraceState(),
    )
    service = EventService()
    sink = ServiceEventSink(service=service, run_id=run_id)  # type: ignore[arg-type]

    with trace.use_span(NonRecordingSpan(context)):
        assert current_trace_id() == f"{trace_id:032x}"
        await sink.append("verification.completed", {"step_id": "step-7"})

    payload = service.events[0][2]
    assert payload["run_id"] == str(run_id)
    assert payload["trace_id"] == f"{trace_id:032x}"
    assert payload["step_id"] == "step-7"


def test_invalid_context_has_no_trace_id() -> None:
    with trace.use_span(NonRecordingSpan(INVALID_SPAN_CONTEXT)):
        assert current_trace_id() is None


def test_api_requests_emit_sdk_spans() -> None:
    provider = trace.get_tracer_provider()
    assert isinstance(provider, TracerProvider)
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    with TestClient(create_app(settings=RuntimeSettings(app_env="test"))) as client:
        assert client.get("/healthz").status_code == 200

    spans = exporter.get_finished_spans()
    assert any(span.name == "http.request" for span in spans)
    http_span = next(span for span in spans if span.name == "http.request")
    assert http_span.attributes["http.request.method"] == "GET"
    assert http_span.attributes["http.response.status_code"] == 200
