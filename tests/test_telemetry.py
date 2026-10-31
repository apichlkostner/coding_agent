"""Tests for OpenTelemetry setup.

These tests exercise endpoint normalization and environment-variable
resolution without contacting a real collector.
"""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import patch

import pytest

from agent import telemetry


class TestResolveOtlpEndpoint:
    _ENV = "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"

    @pytest.fixture(autouse=True)
    def _clear_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(self._ENV, raising=False)

    def _resolve(self, endpoint: str | None) -> str | None:
        return telemetry.resolve_otlp_endpoint(
            endpoint, signal_path="/v1/traces", override_env=self._ENV
        )

    def test_base_url_gets_traces_path(self) -> None:
        assert (
            self._resolve("http://collector:4318") == "http://collector:4318/v1/traces"
        )

    def test_trailing_slash_stripped(self) -> None:
        assert (
            self._resolve("http://collector:4318/") == "http://collector:4318/v1/traces"
        )

    def test_full_traces_url_unchanged(self) -> None:
        url = "http://collector:4318/v1/traces"
        assert self._resolve(url) == url

    def test_full_traces_url_with_trailing_slash_unchanged(self) -> None:
        assert (
            self._resolve("http://collector:4318/v1/traces/")
            == "http://collector:4318/v1/traces"
        )

    def test_override_env_wins_over_endpoint(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(self._ENV, "http://override:4318/v1/traces")
        assert (
            self._resolve("http://collector:4318") == "http://override:4318/v1/traces"
        )

    def test_no_endpoint_and_no_override_returns_none(self) -> None:
        assert self._resolve(None) is None


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
