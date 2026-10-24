"""Tests for the GenAI OpenTelemetry helpers in ``agent.router.otel``.

Spans are captured with an in-memory exporter; no collector is involved.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.outputs import LLMResult
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, StatusCode

from agent import telemetry
from agent.router import otel
from agent.router.messages import InboundMessage
from agent.router.otel import (
    ModelSpanCallback,
    ToolSpanCallback,
    init_agent_span,
    record_run_usage,
    string_preview,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _SpanRecorder:
    """Collect finished spans from a dedicated tracer provider."""

    def __init__(self) -> None:
        self.exporter = InMemorySpanExporter()
        self.provider = TracerProvider()
        self.provider.add_span_processor(SimpleSpanProcessor(self.exporter))

    def spans(self) -> list[Any]:
        return list(self.exporter.get_finished_spans())

    def by_name(self, name: str) -> list[Any]:
        return [s for s in self.spans() if s.name == name]

    def one(self, name: str) -> Any:
        matches = self.by_name(name)
        assert len(matches) == 1, (
            f"expected one span named {name!r}, got {len(matches)}"
        )
        return matches[0]

    def start(self, name: str) -> Any:
        return self.provider.get_tracer("test").start_span(name)


@pytest.fixture
def span_recorder(monkeypatch: pytest.MonkeyPatch) -> _SpanRecorder:
    """Route ``telemetry.get_tracer`` to an in-memory provider."""
    recorder = _SpanRecorder()
    monkeypatch.setattr(
        telemetry, "get_tracer", lambda: recorder.provider.get_tracer("test")
    )
    return recorder


def _attrs(span: Any) -> dict[str, Any]:
    return dict(span.attributes or {})


def _llm_result(llm_output: Any) -> LLMResult:
    return LLMResult(generations=[], llm_output=llm_output)


def _inbound(
    adapter_id: str = "terminal",
    thread_id: str = "thread-1",
) -> InboundMessage:
    return InboundMessage(
        adapter_id=adapter_id,
        thread_id=thread_id,
        content="hello",
        reply_channel_id="stdout",
    )


# ---------------------------------------------------------------------------
# string_preview
# ---------------------------------------------------------------------------


class TestStringPreview:
    def test_short_text_unchanged(self) -> None:
        assert string_preview("short", 10) == "short"

    def test_text_exactly_at_limit_unchanged(self) -> None:
        assert string_preview("12345", 5) == "12345"

    def test_long_text_truncated_with_ellipsis(self) -> None:
        assert string_preview("x" * 20, 5) == "xxxxx…"

    def test_zero_size_disables_truncation(self) -> None:
        text = "x" * 500
        assert string_preview(text, 0) == text

    def test_negative_size_disables_truncation(self) -> None:
        text = "x" * 500
        assert string_preview(text, -1) == text

    def test_empty_text(self) -> None:
        assert string_preview("", 5) == ""


# ---------------------------------------------------------------------------
# _model_name
# ---------------------------------------------------------------------------


class TestModelName:
    def test_invocation_params_model_wins(self) -> None:
        serialized = {"name": "ChatOpenAI", "model": "serialized-model"}
        kwargs = {"invocation_params": {"model": "gpt-5"}}
        assert otel._model_name(serialized, kwargs) == "gpt-5"

    def test_invocation_params_model_name_key(self) -> None:
        kwargs = {"invocation_params": {"model_name": "claude-test"}}
        assert otel._model_name({}, kwargs) == "claude-test"

    def test_serialized_model_fallback(self) -> None:
        assert otel._model_name({"model": "gpt-4o"}, {}) == "gpt-4o"

    def test_serialized_model_name_fallback(self) -> None:
        assert otel._model_name({"model_name": "llama3"}, {}) == "llama3"

    def test_non_dict_invocation_params_ignored(self) -> None:
        kwargs = {"invocation_params": "not-a-dict"}
        assert otel._model_name({"model": "gpt-4o"}, kwargs) == "gpt-4o"

    def test_empty_strings_skipped(self) -> None:
        kwargs = {"invocation_params": {"model": ""}}
        assert otel._model_name({"model_name": "fallback"}, kwargs) == "fallback"

    def test_missing_everywhere_returns_none(self) -> None:
        assert otel._model_name({}, {}) is None

    def test_non_string_value_ignored(self) -> None:
        assert otel._model_name({"model": 42}, {}) is None


# ---------------------------------------------------------------------------
# _provider_name
# ---------------------------------------------------------------------------


class TestProviderName:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (["langchain", "chat_models", "openai"], "openai"),
            (["langchain", "chat_models", "anthropic"], "anthropic"),
            (["langchain", "chat_models", "deepseek"], "deepseek"),
            (["langchain", "chat_models", "google_genai"], "gcp.gen_ai"),
            (["langchain", "chat_models", "groq"], "groq"),
            (["langchain", "chat_models", "mistralai"], "mistral"),
            (["langchain", "chat_models", "ollama"], "ollama"),
        ],
    )
    def test_known_providers_from_id_list(self, raw: list[str], expected: str) -> None:
        assert otel._provider_name({"id": raw}) == expected

    def test_id_string_supported(self) -> None:
        assert otel._provider_name({"id": "langchain_openai"}) == "openai"

    def test_name_used_when_id_missing(self) -> None:
        assert otel._provider_name({"name": "ChatAnthropic"}) == "anthropic"

    def test_matching_is_case_insensitive(self) -> None:
        assert otel._provider_name({"id": ["langchain", "OpenAI"]}) == "openai"

    def test_first_matching_hint_wins(self) -> None:
        """Multiple hints in one name resolve in dict insertion order."""
        assert otel._provider_name({"name": "ChatAnthropicOpenAI"}) == "anthropic"

    def test_unknown_provider_returns_none(self) -> None:
        assert otel._provider_name({"id": ["langchain", "fake_provider"]}) is None

    def test_empty_id_list_returns_none(self) -> None:
        assert otel._provider_name({"id": []}) is None

    def test_non_string_id_returns_none(self) -> None:
        assert otel._provider_name({"id": 123}) is None

    def test_missing_id_and_name_returns_none(self) -> None:
        assert otel._provider_name({}) is None


# ---------------------------------------------------------------------------
# ToolSpanCallback
# ---------------------------------------------------------------------------


class TestToolSpanCallback:
    def test_span_follows_genai_conventions(self, span_recorder: _SpanRecorder) -> None:
        cb = ToolSpanCallback()
        cb.on_tool_start(
            {"name": "bash", "description": "Run a shell command"},
            "ls -la",
            run_id="r1",
        )
        cb.on_tool_end("file.txt", run_id="r1")

        span = span_recorder.one("execute_tool bash")
        assert span.kind == SpanKind.INTERNAL
        assert span.status.status_code == StatusCode.OK
        attrs = _attrs(span)
        assert attrs["gen_ai.operation.name"] == "execute_tool"
        assert attrs["gen_ai.tool.name"] == "bash"
        assert attrs["gen_ai.tool.type"] == "function"
        assert attrs["gen_ai.tool.description"] == "Run a shell command"

    def test_missing_name_defaults_to_tool(self, span_recorder: _SpanRecorder) -> None:
        cb = ToolSpanCallback()
        cb.on_tool_start({}, "input", run_id="r1")
        cb.on_tool_end("output", run_id="r1")

        assert span_recorder.one("execute_tool tool") is not None

    @pytest.mark.parametrize("description", [None, "", 42])
    def test_invalid_description_omitted(
        self, span_recorder: _SpanRecorder, description: Any
    ) -> None:
        cb = ToolSpanCallback()
        cb.on_tool_start(
            {"name": "bash", "description": description}, "ls", run_id="r1"
        )
        cb.on_tool_end("ok", run_id="r1")

        assert "gen_ai.tool.description" not in _attrs(
            span_recorder.one("execute_tool bash")
        )

    def test_payloads_omitted_by_default(self, span_recorder: _SpanRecorder) -> None:
        cb = ToolSpanCallback()
        cb.on_tool_start({"name": "bash"}, "secret-command", run_id="r1")
        cb.on_tool_end("secret-output", run_id="r1")

        attrs = _attrs(span_recorder.one("execute_tool bash"))
        assert "gen_ai.tool.call.arguments" not in attrs
        assert "gen_ai.tool.call.result" not in attrs

    def test_payloads_captured_and_bounded(self, span_recorder: _SpanRecorder) -> None:
        cb = ToolSpanCallback(preview_size=10, capture_payloads=True)
        cb.on_tool_start({"name": "bash"}, "a" * 100, run_id="r1")
        cb.on_tool_end("b" * 100, run_id="r1")

        attrs = _attrs(span_recorder.one("execute_tool bash"))
        assert attrs["gen_ai.tool.call.arguments"] == "a" * 10 + "…"
        assert attrs["gen_ai.tool.call.result"] == "b" * 10 + "…"

    def test_payloads_complete_when_preview_disabled(
        self, span_recorder: _SpanRecorder
    ) -> None:
        cb = ToolSpanCallback(preview_size=0, capture_payloads=True)
        cb.on_tool_start({"name": "bash"}, "a" * 100, run_id="r1")
        cb.on_tool_end("b" * 100, run_id="r1")

        attrs = _attrs(span_recorder.one("execute_tool bash"))
        assert attrs["gen_ai.tool.call.arguments"] == "a" * 100
        assert attrs["gen_ai.tool.call.result"] == "b" * 100

    def test_error_marks_span_and_records_exception(
        self, span_recorder: _SpanRecorder
    ) -> None:
        cb = ToolSpanCallback()
        cb.on_tool_start({"name": "bash"}, "ls", run_id="r1")
        cb.on_tool_error(TimeoutError("too slow"), run_id="r1")

        span = span_recorder.one("execute_tool bash")
        assert span.status.status_code == StatusCode.ERROR
        assert span.status.description == "too slow"
        assert _attrs(span)["error.type"] == "TimeoutError"
        events = [e for e in span.events if e.name == "exception"]
        assert len(events) == 1
        assert events[0].attributes is not None
        assert events[0].attributes["exception.type"] == "TimeoutError"

    def test_end_without_start_is_noop(self, span_recorder: _SpanRecorder) -> None:
        cb = ToolSpanCallback()
        cb.on_tool_end("orphan", run_id="missing")

        assert span_recorder.spans() == []

    def test_error_without_start_is_noop(self, span_recorder: _SpanRecorder) -> None:
        cb = ToolSpanCallback()
        cb.on_tool_error(RuntimeError("boom"), run_id="missing")

        assert span_recorder.spans() == []

    def test_double_end_emits_one_span(self, span_recorder: _SpanRecorder) -> None:
        cb = ToolSpanCallback()
        cb.on_tool_start({"name": "bash"}, "ls", run_id="r1")
        cb.on_tool_end("ok", run_id="r1")
        cb.on_tool_end("ok again", run_id="r1")

        assert len(span_recorder.by_name("execute_tool bash")) == 1

    def test_span_parented_under_supplied_context(
        self, span_recorder: _SpanRecorder
    ) -> None:
        parent = span_recorder.start("parent")
        cb = ToolSpanCallback()
        cb.set_parent_context(trace.set_span_in_context(parent))
        cb.on_tool_start({"name": "bash"}, "ls", run_id="r1")
        cb.on_tool_end("ok", run_id="r1")
        parent.end()

        span = span_recorder.one("execute_tool bash")
        assert span.parent is not None
        assert span.parent.span_id == parent.get_span_context().span_id

    def test_span_without_context_is_root(self, span_recorder: _SpanRecorder) -> None:
        cb = ToolSpanCallback()
        cb.on_tool_start({"name": "bash"}, "ls", run_id="r1")
        cb.on_tool_end("ok", run_id="r1")

        assert span_recorder.one("execute_tool bash").parent is None


# ---------------------------------------------------------------------------
# ModelSpanCallback
# ---------------------------------------------------------------------------


class TestModelSpanCallback:
    def test_span_follows_genai_conventions(self, span_recorder: _SpanRecorder) -> None:
        cb = ModelSpanCallback()
        cb.on_chat_model_start(
            {"name": "ChatOpenAI", "id": ["langchain", "chat_models", "openai"]},
            [[]],
            run_id="m1",
            invocation_params={"model": "gpt-test"},
        )
        cb.on_llm_end(_llm_result(None), run_id="m1")

        span = span_recorder.one("chat gpt-test")
        assert span.kind == SpanKind.CLIENT
        assert span.status.status_code == StatusCode.OK
        attrs = _attrs(span)
        assert attrs["gen_ai.operation.name"] == "chat"
        assert attrs["gen_ai.provider.name"] == "openai"
        assert attrs["gen_ai.request.model"] == "gpt-test"

    def test_span_name_without_model_is_bare_operation(
        self, span_recorder: _SpanRecorder
    ) -> None:
        cb = ModelSpanCallback()
        cb.on_chat_model_start({"name": "ChatUnknown"}, [[]], run_id="m1")
        cb.on_llm_end(_llm_result(None), run_id="m1")

        span = span_recorder.one("chat")
        attrs = _attrs(span)
        assert "gen_ai.request.model" not in attrs
        assert "gen_ai.provider.name" not in attrs

    def test_usage_from_token_usage(self, span_recorder: _SpanRecorder) -> None:
        cb = ModelSpanCallback()
        cb.on_chat_model_start({"name": "ChatOpenAI"}, [[]], run_id="m1")
        cb.on_llm_end(
            _llm_result({"token_usage": {"prompt_tokens": 5, "completion_tokens": 7}}),
            run_id="m1",
        )

        attrs = _attrs(span_recorder.one("chat"))
        assert attrs["gen_ai.usage.input_tokens"] == 5
        assert attrs["gen_ai.usage.output_tokens"] == 7

    def test_usage_from_usage_key(self, span_recorder: _SpanRecorder) -> None:
        cb = ModelSpanCallback()
        cb.on_chat_model_start({"name": "ChatAnthropic"}, [[]], run_id="m1")
        cb.on_llm_end(
            _llm_result({"usage": {"input_tokens": 3, "output_tokens": 4}}),
            run_id="m1",
        )

        attrs = _attrs(span_recorder.one("chat"))
        assert attrs["gen_ai.usage.input_tokens"] == 3
        assert attrs["gen_ai.usage.output_tokens"] == 4

    def test_empty_token_usage_falls_back_to_usage_key(
        self, span_recorder: _SpanRecorder
    ) -> None:
        """An empty ``token_usage`` is falsy, so the ``usage`` key is used."""
        cb = ModelSpanCallback()
        cb.on_chat_model_start({"name": "ChatOpenAI"}, [[]], run_id="m1")
        cb.on_llm_end(
            _llm_result(
                {"token_usage": {}, "usage": {"input_tokens": 2, "output_tokens": 3}}
            ),
            run_id="m1",
        )

        attrs = _attrs(span_recorder.one("chat"))
        assert attrs["gen_ai.usage.input_tokens"] == 2
        assert attrs["gen_ai.usage.output_tokens"] == 3

    def test_boolean_usage_ignored(self, span_recorder: _SpanRecorder) -> None:
        """Booleans are ints in Python and must not be recorded as counts."""
        cb = ModelSpanCallback()
        cb.on_chat_model_start({"name": "ChatOpenAI"}, [[]], run_id="m1")
        cb.on_llm_end(
            _llm_result(
                {"token_usage": {"prompt_tokens": True, "completion_tokens": 7}}
            ),
            run_id="m1",
        )

        attrs = _attrs(span_recorder.one("chat"))
        assert "gen_ai.usage.input_tokens" not in attrs
        assert attrs["gen_ai.usage.output_tokens"] == 7

    def test_total_tokens_has_no_attribute(self, span_recorder: _SpanRecorder) -> None:
        cb = ModelSpanCallback()
        cb.on_chat_model_start({"name": "ChatOpenAI"}, [[]], run_id="m1")
        cb.on_llm_end(
            _llm_result(
                {
                    "token_usage": {
                        "prompt_tokens": 5,
                        "completion_tokens": 7,
                        "total_tokens": 12,
                    }
                }
            ),
            run_id="m1",
        )

        assert "gen_ai.usage.total_tokens" not in _attrs(span_recorder.one("chat"))

    def test_detail_tokens_recorded(self, span_recorder: _SpanRecorder) -> None:
        cb = ModelSpanCallback()
        cb.on_chat_model_start({"name": "ChatOpenAI"}, [[]], run_id="m1")
        cb.on_llm_end(
            _llm_result(
                {
                    "token_usage": {
                        "completion_tokens_details": {"reasoning_tokens": 9},
                        "prompt_tokens_details": {"cached_tokens": 4},
                    }
                }
            ),
            run_id="m1",
        )

        attrs = _attrs(span_recorder.one("chat"))
        assert attrs["gen_ai.usage.reasoning.output_tokens"] == 9
        assert attrs["gen_ai.usage.cache_read.input_tokens"] == 4

    def test_missing_detail_tokens_not_recorded(
        self, span_recorder: _SpanRecorder
    ) -> None:
        cb = ModelSpanCallback()
        cb.on_chat_model_start({"name": "ChatOpenAI"}, [[]], run_id="m1")
        cb.on_llm_end(
            _llm_result(
                {
                    "token_usage": {
                        "completion_tokens_details": {},
                        "prompt_tokens_details": {},
                    }
                }
            ),
            run_id="m1",
        )

        attrs = _attrs(span_recorder.one("chat"))
        assert "gen_ai.usage.reasoning.output_tokens" not in attrs
        assert "gen_ai.usage.cache_read.input_tokens" not in attrs

    def test_non_integer_usage_ignored(self, span_recorder: _SpanRecorder) -> None:
        cb = ModelSpanCallback()
        cb.on_chat_model_start({"name": "ChatOpenAI"}, [[]], run_id="m1")
        cb.on_llm_end(_llm_result({"token_usage": {"prompt_tokens": "5"}}), run_id="m1")

        attrs = _attrs(span_recorder.one("chat"))
        assert "gen_ai.usage.input_tokens" not in attrs

    @pytest.mark.parametrize(
        "llm_output",
        [None, "not-a-dict", {"token_usage": "not-a-dict"}, {}],
    )
    def test_unusable_usage_payload_ignored(
        self, span_recorder: _SpanRecorder, llm_output: Any
    ) -> None:
        cb = ModelSpanCallback()
        cb.on_chat_model_start({"name": "ChatOpenAI"}, [[]], run_id="m1")
        # ``LLMResult`` validates ``llm_output`` as a dict, so use a plain
        # object to exercise the defensive type checks in ``_record_usage``.
        cb.on_llm_end(SimpleNamespace(llm_output=llm_output), run_id="m1")

        span = span_recorder.one("chat")
        assert span.status.status_code == StatusCode.OK
        assert "gen_ai.usage.input_tokens" not in _attrs(span)

    def test_error_marks_span_and_records_exception(
        self, span_recorder: _SpanRecorder
    ) -> None:
        cb = ModelSpanCallback()
        cb.on_chat_model_start({"name": "ChatOpenAI"}, [[]], run_id="m1")
        cb.on_llm_error(ConnectionError("provider down"), run_id="m1")

        span = span_recorder.one("chat")
        assert span.status.status_code == StatusCode.ERROR
        assert span.status.description == "provider down"
        assert _attrs(span)["error.type"] == "ConnectionError"

    def test_end_without_start_is_noop(self, span_recorder: _SpanRecorder) -> None:
        cb = ModelSpanCallback()
        cb.on_llm_end(_llm_result(None), run_id="missing")

        assert span_recorder.spans() == []

    def test_error_without_start_is_noop(self, span_recorder: _SpanRecorder) -> None:
        cb = ModelSpanCallback()
        cb.on_llm_error(RuntimeError("boom"), run_id="missing")

        assert span_recorder.spans() == []

    def test_span_parented_under_supplied_context(
        self, span_recorder: _SpanRecorder
    ) -> None:
        parent = span_recorder.start("parent")
        cb = ModelSpanCallback()
        cb.set_parent_context(trace.set_span_in_context(parent))
        cb.on_chat_model_start({"name": "ChatOpenAI"}, [[]], run_id="m1")
        cb.on_llm_end(_llm_result(None), run_id="m1")
        parent.end()

        span = span_recorder.one("chat")
        assert span.parent is not None
        assert span.parent.span_id == parent.get_span_context().span_id


# ---------------------------------------------------------------------------
# record_run_usage
# ---------------------------------------------------------------------------


class TestRecordRunUsage:
    def _record(self, span_recorder: _SpanRecorder, usage: dict[str, Any]) -> Any:
        span = span_recorder.start("invoke_agent coding-agent")
        record_run_usage(span, usage)
        span.end()
        return span_recorder.one("invoke_agent coding-agent")

    def test_single_model_usage(self, span_recorder: _SpanRecorder) -> None:
        attrs = _attrs(
            self._record(
                span_recorder,
                {"gpt-test": {"input_tokens": 3, "output_tokens": 4}},
            )
        )
        assert attrs["gen_ai.usage.input_tokens"] == 3
        assert attrs["gen_ai.usage.output_tokens"] == 4

    def test_usage_summed_across_models(self, span_recorder: _SpanRecorder) -> None:
        attrs = _attrs(
            self._record(
                span_recorder,
                {
                    "model-a": {"input_tokens": 3, "output_tokens": 4},
                    "model-b": {"input_tokens": 5, "output_tokens": 6},
                },
            )
        )
        assert attrs["gen_ai.usage.input_tokens"] == 8
        assert attrs["gen_ai.usage.output_tokens"] == 10

    def test_total_tokens_ignored(self, span_recorder: _SpanRecorder) -> None:
        attrs = _attrs(
            self._record(
                span_recorder,
                {"gpt-test": {"input_tokens": 3, "total_tokens": 7}},
            )
        )
        assert "gen_ai.usage.total_tokens" not in attrs

    def test_unknown_keys_ignored(self, span_recorder: _SpanRecorder) -> None:
        attrs = _attrs(
            self._record(
                span_recorder,
                {"gpt-test": {"input_tokens": 3, "cache_read": 1}},
            )
        )
        assert attrs == {"gen_ai.usage.input_tokens": 3}

    def test_non_dict_model_usage_ignored(self, span_recorder: _SpanRecorder) -> None:
        attrs = _attrs(self._record(span_recorder, {"gpt-test": 42}))
        assert attrs == {}

    def test_non_integer_values_ignored(self, span_recorder: _SpanRecorder) -> None:
        attrs = _attrs(self._record(span_recorder, {"gpt-test": {"input_tokens": "3"}}))
        assert attrs == {}

    def test_boolean_values_ignored(self, span_recorder: _SpanRecorder) -> None:
        """Booleans are ints in Python and must not be recorded as counts."""
        attrs = _attrs(
            self._record(
                span_recorder,
                {"gpt-test": {"input_tokens": True, "output_tokens": 4}},
            )
        )
        assert attrs == {"gen_ai.usage.output_tokens": 4}

    def test_empty_usage_sets_no_attributes(self, span_recorder: _SpanRecorder) -> None:
        assert _attrs(self._record(span_recorder, {})) == {}


# ---------------------------------------------------------------------------
# init_agent_span
# ---------------------------------------------------------------------------


class TestInitAgentSpan:
    def test_span_follows_genai_conventions(self, span_recorder: _SpanRecorder) -> None:
        with init_agent_span(_inbound(adapter_id="discord", thread_id="t-42")):
            pass

        span = span_recorder.one("invoke_agent coding-agent")
        assert span.kind == SpanKind.INTERNAL
        attrs = _attrs(span)
        assert attrs["gen_ai.operation.name"] == "invoke_agent"
        assert attrs["gen_ai.agent.name"] == "coding-agent"
        assert attrs["gen_ai.conversation.id"] == "t-42"
        assert attrs["thread_id"] == "t-42"
        assert attrs["adapter_id"] == "discord"

    def test_span_is_current_inside_context(self, span_recorder: _SpanRecorder) -> None:
        with init_agent_span(_inbound()) as span:
            current = trace.get_current_span()
            assert current.get_span_context().span_id == span.get_span_context().span_id

        assert span_recorder.one("invoke_agent coding-agent") is not None

    def test_exception_propagates(self, span_recorder: _SpanRecorder) -> None:
        with pytest.raises(RuntimeError, match="boom"):
            with init_agent_span(_inbound()):
                raise RuntimeError("boom")

        span = span_recorder.one("invoke_agent coding-agent")
        assert span.status.status_code == StatusCode.ERROR
        events = [e for e in span.events if e.name == "exception"]
        assert len(events) == 1
        assert events[0].attributes is not None
        assert events[0].attributes["exception.type"] == "RuntimeError"


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


def test_callbacks_are_base_callback_handlers() -> None:
    from langchain_core.callbacks import BaseCallbackHandler

    assert isinstance(ToolSpanCallback(), BaseCallbackHandler)
    assert isinstance(ModelSpanCallback(), BaseCallbackHandler)


def test_span_callbacks_keep_independent_run_maps(
    span_recorder: _SpanRecorder,
) -> None:
    """Same run_id on two callbacks ends each callback's own span only."""
    tool_cb = ToolSpanCallback()
    model_cb = ModelSpanCallback()
    tool_cb.on_tool_start({"name": "bash"}, "ls", run_id="r1")
    model_cb.on_chat_model_start({"name": "ChatOpenAI"}, [[]], run_id="r1")

    # Ending the model run must not close the tool span with the same id.
    model_cb.on_llm_end(_llm_result(None), run_id="r1")
    assert span_recorder.by_name("execute_tool bash") == []
    assert span_recorder.one("chat") is not None

    tool_cb.on_tool_end("ok", run_id="r1")
    span_recorder.one("execute_tool bash")
