from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from opentelemetry.context import Context
from opentelemetry.semconv._incubating.attributes import (
    gen_ai_attributes as gen_ai_attr,
)
from opentelemetry.semconv.attributes import error_attributes as error_attr
from opentelemetry.trace import Span, SpanKind, Status, StatusCode

from agent import telemetry
from agent.router.messages import InboundMessage
from agent.telemetry_metrics import record_token_usage_metrics

logger = logging.getLogger(__name__)

#: Agent name reported as ``gen_ai.agent.name`` and used in the root span name.
_AGENT_NAME = telemetry.SERVICE_NAME

#: ``gen_ai.operation.name`` values from the OpenTelemetry GenAI conventions.
_OP_CHAT = "chat"
_OP_TEXT_COMPLETION = "text_completion"
_OP_EXECUTE_TOOL = "execute_tool"
_OP_INVOKE_AGENT = "invoke_agent"

#: Substrings in a LangChain class name mapped to ``gen_ai.provider.name``.
#: Values follow the well-known provider list of the GenAI conventions.
_PROVIDER_HINTS: dict[str, str] = {
    "anthropic": "anthropic",
    "deepseek": "deepseek",
    "google": "gcp.gen_ai",
    "groq": "groq",
    "mistral": "mistral",
    "ollama": "ollama",
    "openai": "openai",
}

#: Provider token-usage keys mapped to GenAI semantic-convention attributes.
#: ``total_tokens`` has no current convention attribute and is left out.
_GENAI_USAGE_KEYS: dict[str, str] = {
    "prompt_tokens": gen_ai_attr.GEN_AI_USAGE_INPUT_TOKENS,
    "completion_tokens": gen_ai_attr.GEN_AI_USAGE_OUTPUT_TOKENS,
    "input_tokens": gen_ai_attr.GEN_AI_USAGE_INPUT_TOKENS,
    "output_tokens": gen_ai_attr.GEN_AI_USAGE_OUTPUT_TOKENS,
}


class _SpanCallback(BaseCallbackHandler):
    def __init__(self) -> None:
        self._tracer = telemetry.get_tracer()
        self._spans: dict[Any, Span] = {}
        self._parent_context: Context | None = None

    def set_parent_context(self, context: Context) -> None:
        """Set the context tool spans are parented under."""
        self._parent_context = context

    def on_error(self, error: BaseException, *, run_id: Any) -> None:
        span = self._spans.pop(run_id, None)
        if span is None:
            return
        span.record_exception(error)
        # GenAI conventions: ``error.type`` carries a low-cardinality error
        # class, here the exception type name.
        span.set_attribute(error_attr.ERROR_TYPE, type(error).__name__)
        span.set_status(Status(StatusCode.ERROR, str(error)))
        span.end()


class ToolSpanCallback(_SpanCallback):
    """Emit one OpenTelemetry span per tool call.

    ``on_tool_start`` opens the span and stores it keyed by ``run_id``;
    ``on_tool_end`` / ``on_tool_error`` close it.  Spans are non-current
    (no ``use_span``) because the two callbacks run in different contexts.

    Tool inputs and outputs are only attached when *capture_payloads* is
    ``True``, and are always truncated to *preview_size* characters so a
    single tool call cannot export unbounded (or secret-bearing) payloads
    to the collector.
    """

    def __init__(
        self, *, preview_size: int = 200, capture_payloads: bool = False
    ) -> None:
        super().__init__()
        self._preview_size = preview_size
        self._capture_payloads = capture_payloads

    def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        *,
        run_id: Any,
        inputs: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        name = serialized.get("name", "tool")
        span = self._tracer.start_span(
            f"{_OP_EXECUTE_TOOL} {name}",
            context=self._parent_context,
            kind=SpanKind.INTERNAL,
        )
        span.set_attribute(gen_ai_attr.GEN_AI_OPERATION_NAME, _OP_EXECUTE_TOOL)
        span.set_attribute(gen_ai_attr.GEN_AI_TOOL_NAME, name)
        span.set_attribute(gen_ai_attr.GEN_AI_TOOL_TYPE, "function")
        description = serialized.get("description")
        if isinstance(description, str) and description:
            span.set_attribute(gen_ai_attr.GEN_AI_TOOL_DESCRIPTION, description)
        if self._capture_payloads:
            span.set_attribute(
                gen_ai_attr.GEN_AI_TOOL_CALL_ARGUMENTS,
                string_preview(input_str, self._preview_size),
            )
        self._spans[run_id] = span

    def on_tool_end(self, output: Any, *, run_id: Any, **kwargs: Any) -> None:
        span = self._spans.pop(run_id, None)
        if span is None:
            return
        if self._capture_payloads:
            span.set_attribute(
                gen_ai_attr.GEN_AI_TOOL_CALL_RESULT,
                string_preview(str(output), self._preview_size),
            )
        span.set_status(Status(StatusCode.OK))
        span.end()

    def on_tool_error(
        self, error: BaseException, *, run_id: Any, **kwargs: Any
    ) -> None:
        self.on_error(error, run_id=run_id)


class ModelSpanCallback(_SpanCallback):
    """Emit one OpenTelemetry span per model call.

    ``on_chat_model_start`` opens the span and stores it keyed by ``run_id``;
    ``on_llm_end`` / ``on_llm_error`` close it.  The span records the model
    name and, when the provider reports it, token usage.  Spans are
    non-current (no ``use_span``) because the callbacks run in different
    contexts.
    """

    def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[Any]],
        *,
        run_id: Any,
        **kwargs: Any,
    ) -> None:
        model_name = _model_name(serialized, kwargs)
        # GenAI convention: the inference span is named after the operation and
        # the requested model, and is a CLIENT span towards the provider.
        span_name = f"{_OP_CHAT} {model_name}" if model_name else _OP_CHAT
        span = self._tracer.start_span(
            span_name, context=self._parent_context, kind=SpanKind.CLIENT
        )
        span.set_attribute(gen_ai_attr.GEN_AI_OPERATION_NAME, _OP_CHAT)
        provider = _provider_name(serialized)
        if provider:
            span.set_attribute(gen_ai_attr.GEN_AI_PROVIDER_NAME, provider)
        if model_name:
            span.set_attribute(gen_ai_attr.GEN_AI_REQUEST_MODEL, model_name)
        self._spans[run_id] = span

    def on_llm_end(self, response: Any, *, run_id: Any, **kwargs: Any) -> None:
        span = self._spans.pop(run_id, None)
        if span is None:
            return
        _record_usage(span, response)
        span.set_status(Status(StatusCode.OK))
        span.end()

    def on_llm_error(self, error: BaseException, *, run_id: Any, **kwargs: Any) -> None:
        self.on_error(error, run_id=run_id)


class TokenUsageMetricsCallback(BaseCallbackHandler):
    """Record GenAI token-usage metrics for every model call.

    Metrics are independent of :class:`ModelSpanCallback`: they are emitted
    even when no span is created, and cover chat models
    (``on_chat_model_start``) as well as plain LLMs (``on_llm_start``).
    Failed operations record zero-valued per-operation histograms tagged with
    ``error.type``, as the conventions require.
    """

    def __init__(self) -> None:
        self._attributes: dict[Any, dict[str, str]] = {}

    def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[Any]],
        *,
        run_id: Any,
        **kwargs: Any,
    ) -> None:
        self._start(serialized, kwargs, run_id, operation=_OP_CHAT)

    def on_llm_start(
        self,
        serialized: dict[str, Any],
        prompts: list[str],
        *,
        run_id: Any,
        **kwargs: Any,
    ) -> None:
        self._start(serialized, kwargs, run_id, operation=_OP_TEXT_COMPLETION)

    def _start(
        self,
        serialized: dict[str, Any],
        kwargs: dict[str, Any],
        run_id: Any,
        *,
        operation: str,
    ) -> None:
        attributes = {gen_ai_attr.GEN_AI_OPERATION_NAME: operation}
        provider = _provider_name(serialized)
        if provider:
            attributes[gen_ai_attr.GEN_AI_PROVIDER_NAME] = provider
        model_name = _model_name(serialized, kwargs)
        if model_name:
            attributes[gen_ai_attr.GEN_AI_REQUEST_MODEL] = model_name
        self._attributes[run_id] = attributes

    def on_llm_end(self, response: Any, *, run_id: Any, **kwargs: Any) -> None:
        attributes = self._attributes.pop(run_id, None)
        if attributes is None:
            return
        try:
            record_token_usage_metrics(_usage_metadata(response), attributes)
        except Exception:  # noqa: BLE001 - telemetry is best-effort
            logger.debug("Failed to record token usage metrics", exc_info=True)

    def on_llm_error(self, error: BaseException, *, run_id: Any, **kwargs: Any) -> None:
        attributes = self._attributes.pop(run_id, None)
        if attributes is None:
            return
        try:
            record_token_usage_metrics(
                None, attributes, error_type=type(error).__name__
            )
        except Exception:  # noqa: BLE001 - telemetry is best-effort
            logger.debug("Failed to record token usage metrics", exc_info=True)


def _usage_metadata(response: Any) -> dict[str, Any] | None:
    """Return normalized token usage for a single model response.

    LangChain normalizes chat-model usage into ``AIMessage.usage_metadata``
    (``input_tokens``, ``output_tokens``, ``input_token_details.cache_read``,
    ``output_token_details.reasoning``).  Plain LLMs only report provider
    payloads in ``llm_output``; those are mapped onto the same shape.
    """
    generations = getattr(response, "generations", None)
    if isinstance(generations, list) and generations:
        first_group = generations[0]
        if isinstance(first_group, list) and first_group:
            message = getattr(first_group[0], "message", None)
            message_usage = getattr(message, "usage_metadata", None)
            if isinstance(message_usage, Mapping) and message_usage:
                return dict(message_usage)

    llm_output = getattr(response, "llm_output", None)
    if not isinstance(llm_output, Mapping):
        return None
    provider_usage = llm_output.get("token_usage") or llm_output.get("usage")
    if not isinstance(provider_usage, Mapping):
        return None

    usage: dict[str, Any] = {}
    for source_key, target_key in (
        ("prompt_tokens", "input_tokens"),
        ("completion_tokens", "output_tokens"),
        ("input_tokens", "input_tokens"),
        ("output_tokens", "output_tokens"),
    ):
        value = provider_usage.get(source_key)
        if type(value) is int:
            usage[target_key] = value

    input_details: dict[str, int] = {}
    prompt_details = provider_usage.get("prompt_tokens_details")
    if isinstance(prompt_details, Mapping):
        cached_tokens = prompt_details.get("cached_tokens")
        if type(cached_tokens) is int:
            input_details["cache_read"] = cached_tokens
    for source_key, target_key in (
        ("cache_read_input_tokens", "cache_read"),
        ("cache_creation_input_tokens", "cache_creation"),
    ):
        value = provider_usage.get(source_key)
        if type(value) is int:
            input_details[target_key] = value
    if input_details:
        usage["input_token_details"] = input_details

    completion_details = provider_usage.get("completion_tokens_details")
    if isinstance(completion_details, Mapping):
        reasoning_tokens = completion_details.get("reasoning_tokens")
        if type(reasoning_tokens) is int:
            usage["output_token_details"] = {"reasoning": reasoning_tokens}
    return usage or None


def _model_name(serialized: dict[str, Any], kwargs: dict[str, Any]) -> str | None:
    """Best-effort extraction of the model identifier from callback payloads."""
    for source in (kwargs.get("invocation_params"), serialized):
        if isinstance(source, dict):
            for key in ("model", "model_name"):
                value = source.get(key)
                if isinstance(value, str) and value:
                    return value
    return None


def _provider_name(serialized: dict[str, Any]) -> str | None:
    """Best-effort mapping of a LangChain class name to ``gen_ai.provider.name``."""
    raw = serialized.get("id") or serialized.get("name")
    if not isinstance(raw, list | str):
        return None
    text = ".".join(raw) if isinstance(raw, list) else raw
    lowered = text.lower()
    for hint, provider in _PROVIDER_HINTS.items():
        if hint in lowered:
            return provider
    return None


def _record_usage(span: Span, response: Any) -> None:
    """Attach normalized token usage from a model response to *span*.

    Uses the same extraction as the metrics path so traces and metrics report
    identical numbers for the same call.  ``type(value) is int`` rejects
    booleans (a subclass of ``int``) instead of recording 0/1 token counts.
    """
    usage = _usage_metadata(response)
    if not usage:
        return
    input_tokens = usage.get("input_tokens")
    if type(input_tokens) is int:
        span.set_attribute(gen_ai_attr.GEN_AI_USAGE_INPUT_TOKENS, input_tokens)
    output_tokens = usage.get("output_tokens")
    if type(output_tokens) is int:
        span.set_attribute(gen_ai_attr.GEN_AI_USAGE_OUTPUT_TOKENS, output_tokens)
    input_details = usage.get("input_token_details")
    if isinstance(input_details, Mapping):
        cached_tokens = input_details.get("cache_read")
        if type(cached_tokens) is int:
            span.set_attribute(
                gen_ai_attr.GEN_AI_USAGE_CACHE_READ_INPUT_TOKENS, cached_tokens
            )
    output_details = usage.get("output_token_details")
    if isinstance(output_details, Mapping):
        reasoning_tokens = output_details.get("reasoning")
        if type(reasoning_tokens) is int:
            span.set_attribute(
                gen_ai_attr.GEN_AI_USAGE_REASONING_OUTPUT_TOKENS, reasoning_tokens
            )


def record_run_usage(span: Span, usage: dict[str, Any]) -> None:
    """Attach aggregated usage to the ``invoke_agent`` span.

    ``UsageMetadataCallbackHandler.usage_metadata`` maps model name to a
    usage dict (``input_tokens``, ``output_tokens``, ``total_tokens``).  The
    GenAI conventions define one aggregate pair per span, so the per-model
    totals are summed.  ``total_tokens`` is the sum of the other two and has
    no convention attribute of its own.
    """
    totals: dict[str, int] = {}
    for model_usage in usage.values():
        if not isinstance(model_usage, dict):
            continue
        for key, value in model_usage.items():
            gen_ai_key = _GENAI_USAGE_KEYS.get(key)
            if gen_ai_key is not None and type(value) is int:
                totals[gen_ai_key] = totals.get(gen_ai_key, 0) + value
    for gen_ai_key, value in totals.items():
        span.set_attribute(gen_ai_key, value)


def string_preview(text: str, size: int) -> str:
    """Return *text* truncated to *size* characters.

    A non-positive *size* disables truncation and returns *text* unchanged.
    When truncation happens an ellipsis (``…``) is appended so the log makes
    the cut explicit.
    """
    if size <= 0 or len(text) <= size:
        return text
    return text[:size] + "…"


def init_agent_span(message: InboundMessage):
    tracer = telemetry.get_tracer()

    return tracer.start_as_current_span(
        f"{_OP_INVOKE_AGENT} {_AGENT_NAME}",
        kind=SpanKind.INTERNAL,
        attributes={
            gen_ai_attr.GEN_AI_OPERATION_NAME: _OP_INVOKE_AGENT,
            gen_ai_attr.GEN_AI_AGENT_NAME: _AGENT_NAME,
            gen_ai_attr.GEN_AI_CONVERSATION_ID: message.thread_id,
            "agent.adapter.type": message.adapter_id,
        },
    )
