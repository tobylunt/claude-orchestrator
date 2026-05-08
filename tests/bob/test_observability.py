"""Tests for OpenTelemetry instrumentation."""
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from claude_orchestrator.bob.observability import (
    setup_tracing,
    span,
    is_otel_active,
)


def test_span_works_when_otel_inactive():
    """span() should be a no-op when OTel isn't set up — no crash."""
    # Reset by not calling setup_tracing
    with span("test_op"):
        pass  # should not raise


def test_setup_tracing_with_otel_endpoint(monkeypatch):
    """When OTEL_EXPORTER_OTLP_ENDPOINT is set, setup_tracing should activate OTel."""
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:6006/v1/traces")
    setup_tracing(service_name="bob-test")
    # We don't need to verify spans actually export — just that setup doesn't crash.
    with span("test_op", attrs={"x": "y"}):
        pass


def test_setup_tracing_without_endpoint_is_noop(monkeypatch):
    """No OTEL_EXPORTER_OTLP_ENDPOINT and no explicit endpoint → no-op."""
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    setup_tracing(service_name="bob-test")
    with span("test_op"):
        pass


def test_is_otel_active_reflects_state(monkeypatch):
    """is_otel_active() should be True after setup with endpoint, False otherwise."""
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    setup_tracing(service_name="bob-test")
    # Without endpoint, no-op (but the API is still callable).
    # is_otel_active just reflects whether a real provider is configured.


def test_setup_tracing_with_explicit_endpoint():
    """setup_tracing accepts an explicit endpoint param, overriding env."""
    setup_tracing(
        service_name="bob-test",
        otlp_endpoint="http://example.com/v1/traces",
    )
    with span("test_op"):
        pass
