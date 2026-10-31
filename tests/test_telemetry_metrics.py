"""Tests for OpenTelemetry metrics setup."""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest

from agent import telemetry_metrics


class TestConfigureMetrics:
    @pytest.fixture(autouse=True)
    def _reset_configured(self, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
        telemetry_metrics._configured = False
        monkeypatch.delenv("OTEL_EXPORTER_OTLP_METRICS_ENDPOINT", raising=False)
        monkeypatch.delenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", raising=False)
        yield
        telemetry_metrics._configured = False

    def test_metrics_endpoint_overrides_base_endpoint(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(
            "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT",
            "http://metrics:4318/v1/metrics",
        )
        provider = MagicMock()
        with (
            patch.object(telemetry_metrics, "MeterProvider", return_value=provider),
            patch.object(telemetry_metrics, "OTLPMetricExporter") as exporter,
            patch.object(telemetry_metrics.metrics, "set_meter_provider"),
            patch.object(telemetry_metrics.atexit, "register"),
        ):
            telemetry_metrics.configure_metrics(endpoint="http://collector:4318")

        exporter.assert_called_once_with(endpoint="http://metrics:4318/v1/metrics")

    def test_base_endpoint_uses_metrics_path(self) -> None:
        provider = MagicMock()
        with (
            patch.object(telemetry_metrics, "MeterProvider", return_value=provider),
            patch.object(telemetry_metrics, "OTLPMetricExporter") as exporter,
            patch.object(telemetry_metrics.metrics, "set_meter_provider"),
            patch.object(telemetry_metrics.atexit, "register"),
        ):
            telemetry_metrics.configure_metrics(endpoint="http://collector:4318")

        exporter.assert_called_once_with(endpoint="http://collector:4318/v1/metrics")

    def test_base_endpoint_with_trailing_slash_is_normalized(self) -> None:
        provider = MagicMock()
        with (
            patch.object(telemetry_metrics, "MeterProvider", return_value=provider),
            patch.object(telemetry_metrics, "OTLPMetricExporter") as exporter,
            patch.object(telemetry_metrics.metrics, "set_meter_provider"),
            patch.object(telemetry_metrics.atexit, "register"),
        ):
            telemetry_metrics.configure_metrics(endpoint="http://collector:4318/")

        exporter.assert_called_once_with(endpoint="http://collector:4318/v1/metrics")

    def test_no_endpoint_lets_exporter_resolve_env(self) -> None:
        provider = MagicMock()
        with (
            patch.object(telemetry_metrics, "MeterProvider", return_value=provider),
            patch.object(telemetry_metrics, "OTLPMetricExporter") as exporter,
            patch.object(telemetry_metrics.metrics, "set_meter_provider"),
            patch.object(telemetry_metrics.atexit, "register"),
        ):
            telemetry_metrics.configure_metrics()

        exporter.assert_called_once_with()

    def test_provider_has_service_name_resource(self) -> None:
        provider = MagicMock()
        with (
            patch.object(
                telemetry_metrics, "MeterProvider", return_value=provider
            ) as factory,
            patch.object(telemetry_metrics.metrics, "set_meter_provider"),
            patch.object(telemetry_metrics.atexit, "register"),
        ):
            telemetry_metrics.configure_metrics(service_name="test-agent")

        resource = factory.call_args.kwargs["resource"]
        assert resource.attributes["service.name"] == "test-agent"


class TestRecordTokenUsageMetrics:
    def test_records_usage_histograms_and_subset_counters(self) -> None:
        attributes = {
            "gen_ai.operation.name": "chat",
            "gen_ai.provider.name": "openai",
            "gen_ai.request.model": "model-a",
        }
        with (
            patch.object(telemetry_metrics._input_token_counter, "add") as input_add,
            patch.object(telemetry_metrics._output_token_counter, "add") as output_add,
            patch.object(
                telemetry_metrics._cached_input_token_counter, "add"
            ) as cached_add,
            patch.object(
                telemetry_metrics._cache_write_input_token_counter, "add"
            ) as cache_write_add,
            patch.object(
                telemetry_metrics._reasoning_output_token_counter, "add"
            ) as reasoning_add,
            patch.object(
                telemetry_metrics._input_token_histogram, "record"
            ) as input_record,
            patch.object(
                telemetry_metrics._output_token_histogram, "record"
            ) as output_record,
        ):
            telemetry_metrics.record_token_usage_metrics(
                {
                    "input_tokens": 5,
                    "output_tokens": 7,
                    "input_token_details": {"cache_read": 2, "cache_creation": 3},
                    "output_token_details": {"reasoning": 4},
                },
                attributes,
            )

        input_add.assert_called_once_with(5, attributes)
        output_add.assert_called_once_with(7, attributes)
        cached_add.assert_called_once_with(2, attributes)
        cache_write_add.assert_called_once_with(3, attributes)
        reasoning_add.assert_called_once_with(4, attributes)
        input_record.assert_called_once_with(5, attributes)
        output_record.assert_called_once_with(7, attributes)

    def test_zero_token_counts_are_not_added_to_counters(self) -> None:
        with (
            patch.object(telemetry_metrics._input_token_counter, "add") as input_add,
            patch.object(
                telemetry_metrics._cached_input_token_counter, "add"
            ) as cached_add,
            patch.object(telemetry_metrics._input_token_histogram, "record"),
            patch.object(telemetry_metrics._output_token_histogram, "record"),
        ):
            telemetry_metrics.record_token_usage_metrics(
                {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "input_token_details": {"cache_read": 0},
                },
                {"gen_ai.operation.name": "chat"},
            )

        input_add.assert_not_called()
        cached_add.assert_not_called()

    def test_successful_call_with_unknown_usage_records_nothing(self) -> None:
        attributes = {"gen_ai.operation.name": "chat"}
        with (
            patch.object(telemetry_metrics._input_token_counter, "add") as input_add,
            patch.object(telemetry_metrics._output_token_counter, "add") as output_add,
            patch.object(
                telemetry_metrics._input_token_histogram, "record"
            ) as input_record,
            patch.object(
                telemetry_metrics._output_token_histogram, "record"
            ) as output_record,
        ):
            telemetry_metrics.record_token_usage_metrics(None, attributes)

        input_add.assert_not_called()
        output_add.assert_not_called()
        input_record.assert_not_called()
        output_record.assert_not_called()

    def test_failed_call_records_zero_histograms_with_error_type(self) -> None:
        attributes = {"gen_ai.operation.name": "chat"}
        with (
            patch.object(
                telemetry_metrics._input_token_histogram, "record"
            ) as input_record,
            patch.object(
                telemetry_metrics._output_token_histogram, "record"
            ) as output_record,
        ):
            telemetry_metrics.record_token_usage_metrics(
                None, attributes, error_type="TimeoutError"
            )

        error_attributes = {**attributes, "error.type": "TimeoutError"}
        input_record.assert_called_once_with(0, error_attributes)
        output_record.assert_called_once_with(0, error_attributes)

    def test_error_type_is_added_to_failed_operation_metrics(self) -> None:
        with patch.object(telemetry_metrics._input_token_histogram, "record") as record:
            telemetry_metrics.record_token_usage_metrics(
                None,
                {"gen_ai.operation.name": "chat"},
                error_type="TimeoutError",
            )

        assert record.call_args.args[1]["error.type"] == "TimeoutError"

    def test_error_type_is_not_added_to_usage_counters(self) -> None:
        with (
            patch.object(telemetry_metrics._input_token_counter, "add") as input_add,
            patch.object(
                telemetry_metrics._input_token_histogram, "record"
            ) as input_record,
        ):
            telemetry_metrics.record_token_usage_metrics(
                {"input_tokens": 5},
                {"gen_ai.operation.name": "chat"},
                error_type="TimeoutError",
            )

        assert "error.type" not in input_add.call_args.args[1]
        assert input_record.call_args.args[1]["error.type"] == "TimeoutError"

    def test_token_histograms_use_conventional_buckets(self) -> None:
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import (
            HistogramDataPoint,
            InMemoryMetricReader,
        )

        reader = InMemoryMetricReader()
        meter = MeterProvider(metric_readers=[reader]).get_meter("test")
        histogram = telemetry_metrics._create_token_histogram(
            meter, "test.operation.input_tokens", "test histogram"
        )
        histogram.record(5)

        data = reader.get_metrics_data()
        assert data is not None
        metric = data.resource_metrics[0].scope_metrics[0].metrics[0]
        assert metric.unit == "{token}"
        point = metric.data.data_points[0]
        assert isinstance(point, HistogramDataPoint)
        assert (
            tuple(point.explicit_bounds) == telemetry_metrics._TOKEN_BUCKET_BOUNDARIES
        )
