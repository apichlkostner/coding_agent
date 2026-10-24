from __future__ import annotations

import logging
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from opentelemetry.context import Context
from opentelemetry.trace import Span, SpanKind, Status, StatusCode

from agent import telemetry
from agent.router.messages import InboundMessage

logger = logging.getLogger(__name__)

#: Agent name reported as ``gen_ai.agent.name`` and used in the root span name.
_AGENT_NAME = telemetry.SERVICE_NAME

#: ``gen_ai.operation.name`` values from the OpenTelemetry GenAI conventions.
_OP_CHAT = "chat"
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
    "prompt_tokens": "gen_ai.usage.input_tokens",
    "completion_tokens": "gen_ai.usage.output_tokens",
    "input_tokens": "gen_ai.usage.input_tokens",
    "output_tokens": "gen_ai.usage.output_tokens",
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
        span.set_attribute("error.type", type(error).__name__)
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
        span.set_attribute("gen_ai.operation.name", _OP_EXECUTE_TOOL)
        span.set_attribute("gen_ai.tool.name", name)
        span.set_attribute("gen_ai.tool.type", "function")
        description = serialized.get("description")
        if isinstance(description, str) and description:
            span.set_attribute("gen_ai.tool.description", description)
        if self._capture_payloads:
            span.set_attribute(
                "gen_ai.tool.call.arguments",
                string_preview(input_str, self._preview_size),
            )
        self._spans[run_id] = span

    def on_tool_end(self, output: Any, *, run_id: Any, **kwargs: Any) -> None:
        span = self._spans.pop(run_id, None)
        if span is None:
            return
        if self._capture_payloads:
            span.set_attribute(
                "gen_ai.tool.call.result",
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

    def __init__(self) -> None:
        super().__init__()

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
        span.set_attribute("gen_ai.operation.name", _OP_CHAT)
        provider = _provider_name(serialized)
        if provider:
            span.set_attribute("gen_ai.provider.name", provider)
        if model_name:
            span.set_attribute("gen_ai.request.model", model_name)
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
    """Attach token usage from an LLMResult to *span*, when available."""
    llm_output = getattr(response, "llm_output", None)
    if not isinstance(llm_output, dict):
        return
    usage = llm_output.get("token_usage") or llm_output.get("usage")
    if not isinstance(usage, dict):
        return
    for key, value in usage.items():
        # ``type(value) is int`` so booleans (a subclass of ``int``) are
        # rejected rather than recorded as 0/1 token counts.
        if type(value) is int:
            # GenAI semantic conventions: standard token accounting attributes.
            gen_ai_key = _GENAI_USAGE_KEYS.get(key)
            if gen_ai_key is not None:
                span.set_attribute(gen_ai_key, value)
        if isinstance(value, dict):
            if (
                key == "completion_tokens_details"
                and (reasoning_tokens := value.get("reasoning_tokens")) is not None
            ):
                span.set_attribute(
                    "gen_ai.usage.reasoning.output_tokens", reasoning_tokens
                )
            if (
                key == "prompt_tokens_details"
                and (cached_tokens := value.get("cached_tokens")) is not None
            ):
                span.set_attribute(
                    "gen_ai.usage.cache_read.input_tokens", cached_tokens
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
            "gen_ai.operation.name": _OP_INVOKE_AGENT,
            "gen_ai.agent.name": _AGENT_NAME,
            "gen_ai.conversation.id": message.thread_id,
            "agent.adapter.type": message.adapter_id,
        },
    )
