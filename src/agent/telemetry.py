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

#: Service and agent identity, used as ``service.name``,
#: ``gen_ai.agent.name`` and the tracer scope name.
SERVICE_NAME = "coding-agent"

#: Path appended to a base OTLP endpoint to reach the traces signal.
_TRACES_PATH = "/v1/traces"

#: Traces-specific endpoint override; takes precedence over the generic one.
_TRACES_ENDPOINT_ENV = "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"


def resolve_otlp_endpoint(
    endpoint: str | None,
    *,
    signal_path: str,
    override_env: str,
) -> str | None:
    """Return the full URL of one OTLP signal, or ``None`` to defer to the exporter.

    *override_env* is a signal-specific full URL and wins over *endpoint*.
    *endpoint* is a base URL (``http://collector:4318``) and gets *signal_path*
    appended unless it already ends with it.
    """
    override = os.environ.get(override_env)
    if override:
        return override
    if not endpoint:
        return None
    stripped = endpoint.rstrip("/")
    if stripped.endswith(signal_path):
        return stripped
    return stripped + signal_path


def configure_telemetry(
    *,
    service_name: str = SERVICE_NAME,
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
    resolved = resolve_otlp_endpoint(
        endpoint, signal_path=_TRACES_PATH, override_env=_TRACES_ENDPOINT_ENV
    )
    # Without a URL the exporter reads OTEL_EXPORTER_OTLP_* itself.
    exporter = OTLPSpanExporter(endpoint=resolved) if resolved else OTLPSpanExporter()
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    atexit.register(provider.shutdown)  # flush buffered spans on exit


def get_tracer() -> trace.Tracer:
    """Return the agent tracer."""
    return trace.get_tracer(SERVICE_NAME)
