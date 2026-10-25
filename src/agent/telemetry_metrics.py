import atexit
import os
from collections.abc import Mapping
from typing import Any

from opentelemetry import metrics
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource

from agent.telemetry import SERVICE_NAME

#: Path appended to a base OTLP endpoint to reach the metrics signal.
_METRICS_PATH = "/v1/metrics"

#: Metrics-specific endpoint override; takes precedence over the generic one.
_METRICS_ENDPOINT_ENV = "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT"

_configured = False


def get_metrics() -> metrics.Meter:
    """Return the agent metrics."""
    return metrics.get_meter(SERVICE_NAME)


#: Histogram boundaries recommended by the GenAI token-metric conventions.
#: The SDK defaults stop at 10k tokens, which collapses realistic prompts
#: into the last bucket and makes percentiles meaningless.
_TOKEN_BUCKET_BOUNDARIES: tuple[float, ...] = (
    1,
    4,
    16,
    64,
    256,
    1024,
    4096,
    16384,
    65536,
    262144,
    1048576,
    4194304,
    16777216,
    67108864,
)

_meter = get_metrics()

_input_token_counter = _meter.create_counter(
    "gen_ai.client.inference.usage.input_tokens",
    unit="{token}",
    description="Input tokens used by client inference operations",
)
_output_token_counter = _meter.create_counter(
    "gen_ai.client.inference.usage.output_tokens",
    unit="{token}",
    description="Output tokens used by client inference operations",
)
_cached_input_token_counter = _meter.create_counter(
    "gen_ai.client.inference.usage.cache_read.input_tokens",
    unit="{token}",
    description="Input tokens served from a provider-managed cache",
)
_cache_write_input_token_counter = _meter.create_counter(
    "gen_ai.client.inference.usage.cache_write.input_tokens",
    unit="{token}",
    description="Input tokens written to a provider-managed cache",
)
_reasoning_output_token_counter = _meter.create_counter(
    "gen_ai.client.inference.usage.reasoning.output_tokens",
    unit="{token}",
    description="Output tokens used for model reasoning",
)


def _create_token_histogram(
    meter: metrics.Meter, name: str, description: str
) -> metrics.Histogram:
    """Create a per-operation token histogram with the conventional buckets."""
    return meter.create_histogram(
        name,
        unit="{token}",
        description=description,
        explicit_bucket_boundaries_advisory=_TOKEN_BUCKET_BOUNDARIES,
    )


_input_token_histogram = _create_token_histogram(
    _meter,
    "gen_ai.client.inference.operation.input_tokens",
    "Input tokens used per inference operation",
)
_output_token_histogram = _create_token_histogram(
    _meter,
    "gen_ai.client.inference.operation.output_tokens",
    "Output tokens used per inference operation",
)


def _normalize_endpoint(endpoint: str) -> str:
    """Return the full metrics URL for *endpoint*.

    ``OTEL_EXPORTER_OTLP_ENDPOINT`` is a *base* URL (e.g.
    ``http://collector:4318``), while :class:`OTLPMetricExporter` expects the
    full metrics URL (``http://collector:4318/v1/metrics``). A value that
    already points at the metrics signal is returned unchanged.
    """
    stripped = endpoint.rstrip("/")
    if stripped.endswith(_METRICS_PATH):
        return stripped
    return stripped + _METRICS_PATH


def configure_telemetry_metric(
    *,
    service_name: str = SERVICE_NAME,
    endpoint: str | None = None,
) -> None:
    """Install the global meter provider. Idempotent."""
    global _configured
    if _configured:
        return
    _configured = True

    provider = MeterProvider(resource=Resource.create({"service.name": service_name}))

    metrics_endpoint = os.environ.get(_METRICS_ENDPOINT_ENV)
    if metrics_endpoint:
        # The metrics-specific override wins and is already a full signal URL.
        provider.add_metric_reader(
            PeriodicExportingMetricReader(OTLPMetricExporter(endpoint=metrics_endpoint))
        )
    elif endpoint:
        provider.add_metric_reader(
            PeriodicExportingMetricReader(
                OTLPMetricExporter(endpoint=_normalize_endpoint(endpoint))
            )
        )

    # Sets the global default meter provider
    metrics.set_meter_provider(provider)
    atexit.register(provider.shutdown)  # flush buffered metrics on exit


def _add_token_counter(
    counter: metrics.Counter, token_count: Any, attributes: Mapping[str, str]
) -> None:
    """Add *token_count* to *counter* when it is known and strictly positive."""
    if type(token_count) is int and token_count > 0:
        counter.add(token_count, attributes)


def record_token_usage_metrics(
    usage: Mapping[str, Any] | None,
    attributes: Mapping[str, str],
    *,
    error_type: str | None = None,
) -> None:
    """Record GenAI token consumption and per-inference distributions.

    Cached and reasoning tokens are reported as subsets of the input and
    output usage; they are never added to the totals a second time. Missing
    counts are recorded as zero in the per-operation histograms, while
    cumulative usage counters only record known positive token counts.
    ``error.type`` is only added to the histograms, as the conventions define
    it there and a failed operation has no token count to add to a counter.
    """
    usage = usage or {}
    histogram_attributes = dict(attributes)
    if error_type is not None:
        histogram_attributes["error.type"] = error_type
    # Counters only carry attributes defined for them; ``error.type`` belongs
    # to the per-operation histograms, and failed calls add no token counts.
    usage_attributes = {
        **dict(attributes),
        "gen_ai.token.modality": "unknown",
    }

    for key, counter, histogram in (
        ("input_tokens", _input_token_counter, _input_token_histogram),
        ("output_tokens", _output_token_counter, _output_token_histogram),
    ):
        token_count = usage.get(key)
        count = token_count if type(token_count) is int and token_count >= 0 else 0
        histogram.record(count, histogram_attributes)
        if count > 0:
            counter.add(count, usage_attributes)

    input_details = usage.get("input_token_details")
    if isinstance(input_details, Mapping):
        _add_token_counter(
            _cached_input_token_counter,
            input_details.get("cache_read"),
            usage_attributes,
        )
        _add_token_counter(
            _cache_write_input_token_counter,
            input_details.get("cache_creation"),
            usage_attributes,
        )

    output_details = usage.get("output_token_details")
    if isinstance(output_details, Mapping):
        _add_token_counter(
            _reasoning_output_token_counter,
            output_details.get("reasoning"),
            usage_attributes,
        )
