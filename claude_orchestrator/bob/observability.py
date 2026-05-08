"""OpenTelemetry instrumentation for Bob.

Spans are emitted at:
- Phase entry/exit (Coordinator, McLoop iter, Orchestra round, Vroom cycle)
- Model calls (provider, model, duration)
- Verifier results (status, reason)
- HITL gate events (gate name, decision)

The instrumentation is opt-in: setup_tracing() configures an OTLP exporter
based on OTEL_EXPORTER_OTLP_ENDPOINT (or an explicit param). Without that,
all span() calls are no-ops — safe to instrument unconditionally without
adding latency or required dependencies.

Recommended local backend: Phoenix (Arize, OSS).
Run `arize-phoenix --port 6006` and set
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:6006/v1/traces.
"""

from __future__ import annotations

import contextlib
import os
from typing import Any, Iterator


_otel_active = False
_tracer = None


def setup_tracing(
    *,
    service_name: str = "bob",
    otlp_endpoint: str | None = None,
) -> None:
    """Configure OpenTelemetry. Idempotent. No-op if no endpoint configured."""
    global _otel_active, _tracer

    endpoint = otlp_endpoint or os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not endpoint:
        # No endpoint: leave as no-op so span() calls are cheap.
        return

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        # OTel deps not installed — skip silently. User can install via .[m2] or .[m4].
        return

    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(endpoint=endpoint)
    processor = BatchSpanProcessor(exporter)
    provider.add_span_processor(processor)

    trace.set_tracer_provider(provider)
    _tracer = trace.get_tracer(service_name)
    _otel_active = True


def is_otel_active() -> bool:
    return _otel_active


@contextlib.contextmanager
def span(
    name: str,
    *,
    attrs: dict[str, Any] | None = None,
) -> Iterator[None]:
    """Emit a span if OTel is active; no-op otherwise.

    Use as a context manager:
        with span("mcloop.iteration", attrs={"feature_id": 1, "iter": 3}):
            ...
    """
    if not _otel_active or _tracer is None:
        yield
        return

    with _tracer.start_as_current_span(name) as s:
        if attrs:
            for k, v in attrs.items():
                s.set_attribute(k, v)
        yield
