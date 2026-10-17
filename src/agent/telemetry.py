"""OpenTelemetry setup. Call ``configure_telemetry`` once at startup."""

from __future__ import annotations

import atexit
import logging
import os

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

logger = logging.getLogger(__name__)

_configured = False

#: Path appended to a base OTLP endpoint to reach the traces signal.
_TRACES_PATH = "/v1/traces"

#: Traces-specific endpoint override; takes precedence over the generic one.
_TRACES_ENDPOINT_ENV = "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"


def _normalize_endpoint(endpoint: str) -> str:
    """Return the full traces URL for *endpoint*.

    ``OTEL_EXPORTER_OTLP_ENDPOINT`` is a *base* URL (e.g.
    ``http://collector:4318``), while :class:`OTLPSpanExporter` expects the
    full traces URL (``http://collector:4318/v1/traces``).  A value that
    already points at the traces signal is returned unchanged.
    """
    stripped = endpoint.rstrip("/")
    if stripped.endswith(_TRACES_PATH):
        return stripped
    return stripped + _TRACES_PATH


def configure_telemetry(
    *,
    service_name: str = "coding-agent",
    endpoint: str | None = None,
) -> None:
    """Install the global tracer provider. Idempotent.

    Parameters
    ----------
    service_name:
        Value reported as the ``service.name`` resource attribute.
    endpoint:
        Base OTLP endpoint (e.g. ``http://collector:4318``).  When ``None``
        the exporter resolves the standard ``OTEL_EXPORTER_OTLP_*``
        environment variables itself.  When provided, a base URL is
        normalized to the full traces URL.

    ``OTEL_EXPORTER_OTLP_TRACES_ENDPOINT`` always takes precedence over
    *endpoint* (and over the generic ``OTEL_EXPORTER_OTLP_ENDPOINT``), per
    the OpenTelemetry specification.
    """
    global _configured
    if _configured:
        return
    _configured = True

    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    traces_endpoint = os.environ.get(_TRACES_ENDPOINT_ENV)
    if traces_endpoint:
        # Traces-specific override wins; it is already a full traces URL.
        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=traces_endpoint))
        )
    elif endpoint:
        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=_normalize_endpoint(endpoint)))
        )
    else:
        # No explicit endpoint: let the exporter read the standard
        # OTEL_EXPORTER_OTLP_ENDPOINT environment variable.
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    atexit.register(provider.shutdown)  # flush buffered spans on exit


def get_tracer() -> trace.Tracer:
    """Return the agent tracer."""
    return trace.get_tracer("coding-agent")
