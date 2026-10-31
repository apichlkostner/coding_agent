import atexit
from collections.abc import Mapping
from typing import Any

from opentelemetry import metrics
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource

from agent.telemetry import SERVICE_NAME, resolve_otlp_endpoint

#: Path appended to a base OTLP endpoint to reach the metrics signal.
_METRICS_PATH = "/v1/metrics"

#: Metrics-specific endpoint override; takes precedence over the generic one.
_METRICS_ENDPOINT_ENV = "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT"

_configured = False


def get_meter() -> metrics.Meter:
    """Return the agent meter."""
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

_meter = get_meter()

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


def configure_metrics(
    *,
    service_name: str = SERVICE_NAME,
    endpoint: str | None = None,
) -> None:
    """Install the global meter provider. Idempotent.

    Endpoint resolution matches :func:`agent.telemetry.configure_telemetry`:
    ``OTEL_EXPORTER_OTLP_METRICS_ENDPOINT`` wins, then *endpoint*, then the
    exporter's own ``OTEL_EXPORTER_OTLP_*`` lookup.
    """
    global _configured
    if _configured:
        return
    _configured = True

    provider = MeterProvider(resource=Resource.create({"service.name": service_name}))
    resolved = resolve_otlp_endpoint(
        endpoint, signal_path=_METRICS_PATH, override_env=_METRICS_ENDPOINT_ENV
    )
    exporter = (
        OTLPMetricExporter(endpoint=resolved) if resolved else OTLPMetricExporter()
    )
    provider.add_metric_reader(PeriodicExportingMetricReader(exporter))

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
    output usage; they are never added to the totals a second time.
    A failed operation records zero in the per-operation histograms with
    ``error.type``. A successful operation with unknown usage records nothing,
    so it does not skew the percentiles. Counters only add known positive counts.
    """
    usage = usage or {}
    histogram_attributes = dict(attributes)
    if error_type is not None:
        histogram_attributes["error.type"] = error_type

    for key, counter, histogram in (
        ("input_tokens", _input_token_counter, _input_token_histogram),
        ("output_tokens", _output_token_counter, _output_token_histogram),
    ):
        token_count = usage.get(key)
        if type(token_count) is int and token_count >= 0:
            count = token_count
        elif error_type is not None:
            count = 0
        else:
            continue
        histogram.record(count, histogram_attributes)
        if count > 0:
            counter.add(count, attributes)

    input_details = usage.get("input_token_details")
    if isinstance(input_details, Mapping):
        _add_token_counter(
            _cached_input_token_counter, input_details.get("cache_read"), attributes
        )
        _add_token_counter(
            _cache_write_input_token_counter,
            input_details.get("cache_creation"),
            attributes,
        )

    output_details = usage.get("output_token_details")
    if isinstance(output_details, Mapping):
        _add_token_counter(
            _reasoning_output_token_counter, output_details.get("reasoning"), attributes
        )
