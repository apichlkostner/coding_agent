"""Tests for OpenTelemetry setup.

These tests exercise endpoint normalization and environment-variable
resolution without contacting a real collector.
"""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import patch

import pytest

from agent import telemetry


class TestNormalizeEndpoint:
    def test_base_url_gets_traces_path(self) -> None:
        assert (
            telemetry._normalize_endpoint("http://collector:4318")
            == "http://collector:4318/v1/traces"
        )

    def test_trailing_slash_stripped(self) -> None:
        assert (
            telemetry._normalize_endpoint("http://collector:4318/")
            == "http://collector:4318/v1/traces"
        )

    def test_full_traces_url_unchanged(self) -> None:
        url = "http://collector:4318/v1/traces"
        assert telemetry._normalize_endpoint(url) == url

    def test_full_traces_url_with_trailing_slash_unchanged(self) -> None:
        assert (
            telemetry._normalize_endpoint("http://collector:4318/v1/traces/")
            == "http://collector:4318/v1/traces"
        )


class TestConfigureTelemetry:
    @pytest.fixture(autouse=True)
    def _reset_configured(self, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
        """Reset the idempotency flag and clear the traces override env var."""
        telemetry._configured = False
        monkeypatch.delenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", raising=False)
        yield
        telemetry._configured = False

    def test_explicit_endpoint_is_normalized(self) -> None:
        with patch.object(telemetry, "OTLPSpanExporter") as exporter:
            telemetry.configure_telemetry(endpoint="http://collector:4318")
        exporter.assert_called_once_with(endpoint="http://collector:4318/v1/traces")

    def test_no_endpoint_lets_exporter_resolve_env(self) -> None:
        with patch.object(telemetry, "OTLPSpanExporter") as exporter:
            telemetry.configure_telemetry()
        # No explicit endpoint: the exporter reads the standard env vars.
        exporter.assert_called_once_with()

    def test_traces_endpoint_overrides_explicit_endpoint(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(
            "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "http://traces:4318/v1/traces"
        )
        with patch.object(telemetry, "OTLPSpanExporter") as exporter:
            telemetry.configure_telemetry(endpoint="http://collector:4318")
        # The traces-specific override wins over the generic base URL.
        exporter.assert_called_once_with(endpoint="http://traces:4318/v1/traces")

    def test_traces_endpoint_used_without_explicit_endpoint(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(
            "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "http://traces:4318/v1/traces"
        )
        with patch.object(telemetry, "OTLPSpanExporter") as exporter:
            telemetry.configure_telemetry()
        exporter.assert_called_once_with(endpoint="http://traces:4318/v1/traces")

    def test_idempotent(self) -> None:
        with patch.object(telemetry, "OTLPSpanExporter") as exporter:
            telemetry.configure_telemetry(endpoint="http://collector:4318")
            telemetry.configure_telemetry(endpoint="http://other:4318")
        exporter.assert_called_once()
