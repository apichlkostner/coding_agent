"""Agent service — the single place that talks to the LangGraph graph.

All stream-processing logic (step parsing, message classification, error
handling) lives here.  Adapters never touch the graph directly.
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncGenerator
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler, UsageMetadataCallbackHandler
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph
from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.trace import Span, Status, StatusCode

from agent import telemetry
from agent.router.messages import InboundMessage, OutboundMessage

logger = logging.getLogger(__name__)


class ToolSpanCallback(BaseCallbackHandler):
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
        self._tracer = telemetry.get_tracer()
        self._spans: dict[Any, Span] = {}
        self._parent_context: Context | None = None
        self._preview_size = preview_size
        self._capture_payloads = capture_payloads

    def set_parent_context(self, context: Context) -> None:
        """Set the context tool spans are parented under.

        Called by :meth:`AgentService.run` with the ``agent.run`` span's
        context so tool spans nest under it instead of becoming roots.
        """
        self._parent_context = context

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
        span = self._tracer.start_span(f"tool.{name}", context=self._parent_context)
        span.set_attribute("tool.name", name)
        if self._capture_payloads:
            span.set_attribute("tool.input", _preview(input_str, self._preview_size))
            if inputs is not None:
                for key, value in inputs.items():
                    span.set_attribute(
                        f"tool.args.{key}", _preview(str(value), self._preview_size)
                    )
        self._spans[run_id] = span

    def on_tool_end(self, output: Any, *, run_id: Any, **kwargs: Any) -> None:
        span = self._spans.pop(run_id, None)
        if span is None:
            return
        if self._capture_payloads:
            span.set_attribute("tool.output", _preview(str(output), self._preview_size))
        span.set_status(Status(StatusCode.OK))
        span.end()

    def on_tool_error(
        self, error: BaseException, *, run_id: Any, **kwargs: Any
    ) -> None:
        span = self._spans.pop(run_id, None)
        if span is None:
            return
        span.record_exception(error)
        span.set_status(Status(StatusCode.ERROR, str(error)))
        span.end()


class ModelSpanCallback(BaseCallbackHandler):
    """Emit one OpenTelemetry span per model call.

    ``on_chat_model_start`` opens the span and stores it keyed by ``run_id``;
    ``on_llm_end`` / ``on_llm_error`` close it.  The span records the model
    name and, when the provider reports it, token usage.  Spans are
    non-current (no ``use_span``) because the callbacks run in different
    contexts.
    """

    def __init__(self) -> None:
        self._tracer = telemetry.get_tracer()
        self._spans: dict[Any, Span] = {}
        self._parent_context: Context | None = None

    def set_parent_context(self, context: Context) -> None:
        """Set the context model spans are parented under."""
        self._parent_context = context

    def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[Any]],
        *,
        run_id: Any,
        **kwargs: Any,
    ) -> None:
        span = self._tracer.start_span("model.call", context=self._parent_context)
        model_name = _model_name(serialized, kwargs)
        if model_name:
            span.set_attribute("model.name", model_name)
        self._spans[run_id] = span

    def on_llm_end(self, response: Any, *, run_id: Any, **kwargs: Any) -> None:
        span = self._spans.pop(run_id, None)
        if span is None:
            return
        _record_usage(span, response)
        span.set_status(Status(StatusCode.OK))
        span.end()

    def on_llm_error(self, error: BaseException, *, run_id: Any, **kwargs: Any) -> None:
        span = self._spans.pop(run_id, None)
        if span is None:
            return
        span.record_exception(error)
        span.set_status(Status(StatusCode.ERROR, str(error)))
        span.end()


def _model_name(serialized: dict[str, Any], kwargs: dict[str, Any]) -> str | None:
    """Best-effort extraction of the model identifier from callback payloads."""
    for source in (kwargs.get("invocation_params"), serialized):
        if isinstance(source, dict):
            for key in ("model", "model_name"):
                value = source.get(key)
                if isinstance(value, str) and value:
                    return value
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
        if isinstance(value, int):
            span.set_attribute(f"model.usage.{key}", value)


def _record_run_usage(span: Span, usage: dict[str, Any]) -> None:
    """Attach aggregated per-model usage to the ``agent.run`` span.

    ``UsageMetadataCallbackHandler.usage_metadata`` maps model name to a
    usage dict (``input_tokens``, ``output_tokens``, ``total_tokens``).
    """
    for model_name, model_usage in usage.items():
        if not isinstance(model_usage, dict):
            continue
        for key, value in model_usage.items():
            if isinstance(value, int):
                span.set_attribute(f"model.usage.{model_name}.{key}", value)


def _preview(text: str, size: int) -> str:
    """Return *text* truncated to *size* characters.

    A non-positive *size* disables truncation and returns *text* unchanged.
    When truncation happens an ellipsis (``…``) is appended so the log makes
    the cut explicit.
    """
    if size <= 0 or len(text) <= size:
        return text
    return text[:size] + "…"


class AgentService:
    """Wraps the LangGraph graph and streams :class:`OutboundMessage` objects.

    Parameters
    ----------
    graph:
        A compiled LangGraph ``StateGraph``.  The module-level singleton from
        ``agent.graph`` is the normal value; tests may pass a mock.
    verbose:
        When ``True`` (default), ``tool_call`` and ``tool_result`` messages
        are yielded in addition to the final ``response``.  Set to ``False``
        to surface only the agent's final reply.
    preview_size:
        Maximum number of characters of message content written to the log.
        ``0`` or a negative value logs the full content.
    capture_payloads:
        When ``True``, tool inputs and outputs are attached to tool spans
        (bounded by *preview_size*).  Defaults to ``False`` so commands, file
        contents, or secrets in tool results are not exported to the
        configured collector.
    """

    def __init__(
        self,
        graph: CompiledStateGraph,
        *,
        verbose: bool = True,
        preview_size: int = 200,
        capture_payloads: bool = False,
    ) -> None:
        self._graph = graph
        self._verbose = verbose
        self._preview_size = preview_size
        self._capture_payloads = capture_payloads

    async def run(
        self, message: InboundMessage
    ) -> AsyncGenerator[OutboundMessage, None]:
        """Stream agent outputs for *message*, yielding one :class:`OutboundMessage`
        per meaningful event.

        Yields
        ------
        OutboundMessage
            - ``msg_type="tool_call"`` — agent requested tool(s) (verbose only).
            - ``msg_type="tool_result"`` — tool execution result (verbose only).
            - ``msg_type="response"`` — final agent text response.
            - ``msg_type="error"`` — unrecoverable error during processing.

        The ``adapter_id`` and ``reply_channel_id`` on every yielded message
        are copied from *message* so the router knows where to send it.
        """

        def _make(content: str, msg_type: str, node_name: str = "") -> OutboundMessage:
            meta = {**message.metadata, "msg_type": msg_type, "node_name": node_name}
            return OutboundMessage(
                adapter_id=message.adapter_id,
                reply_channel_id=message.reply_channel_id,
                content=content,
                metadata=meta,
            )

        usage_metadata_cb = UsageMetadataCallbackHandler()
        tool_span_cb = ToolSpanCallback(
            preview_size=self._preview_size,
            capture_payloads=self._capture_payloads,
        )
        model_span_cb = ModelSpanCallback()

        config: RunnableConfig = {
            "configurable": {"thread_id": message.thread_id},
            "callbacks": [usage_metadata_cb, tool_span_cb, model_span_cb],
        }

        start_time = time.perf_counter()

        logger.info(
            "%s",
            message.content,
            extra={"event": "user-message"},
        )

        tracer = telemetry.get_tracer()
        with tracer.start_as_current_span("agent.run") as span:
            span.set_attribute("thread_id", message.thread_id)
            span.set_attribute("adapter_id", message.adapter_id)
            # Parent tool and model spans under this span; the callbacks run
            # outside this context, so they cannot pick up the parent implicitly.
            parent_context = trace.set_span_in_context(span)
            tool_span_cb.set_parent_context(parent_context)
            model_span_cb.set_parent_context(parent_context)
            try:
                async for step in self._graph.astream(
                    {"messages": [HumanMessage(content=message.content)]},
                    stream_mode="updates",
                    config=config,
                ):
                    node_name, node_output = next(iter(step.items()))

                    # Some internal nodes (e.g. __interrupt__) don't carry messages.
                    if "messages" not in node_output:
                        continue

                    last_msg = node_output["messages"][-1]

                    if hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
                        for tc in last_msg.tool_calls:
                            logger.info(
                                "%s",
                                tc["args"],
                                extra={
                                    "event": {"type": "tool-call", "tool": tc["name"]}
                                },
                            )
                        calls = ", ".join(
                            tc["name"]
                            + "("
                            + _preview(str(tc["args"]), self._preview_size)
                            + ")"
                            for tc in last_msg.tool_calls
                        )
                        if self._verbose:
                            yield _make(calls, "tool_call", node_name)

                    elif hasattr(last_msg, "name") and last_msg.name is not None:
                        # ToolMessage(s) — one execution result per requested call.
                        # ``ToolNode`` returns every result in a single update, so
                        # iterate the whole list instead of only the last message.
                        for tool_msg in node_output["messages"]:
                            preview = _preview(tool_msg.content, self._preview_size)
                            logger.info(
                                "%s",
                                preview,
                                extra={
                                    "event": {
                                        "type": "tool-result",
                                        "tool": getattr(tool_msg, "name", ""),
                                    }
                                },
                            )
                            if self._verbose:
                                yield _make(preview, "tool_result", node_name)

                    else:
                        # Final agent response
                        runtime = round(time.perf_counter() - start_time, 2)
                        usage = usage_metadata_cb.usage_metadata
                        _record_run_usage(span, usage)
                        logger.info(
                            "%s",
                            _preview(last_msg.content, self._preview_size),
                            extra={
                                "event": "agent-response",
                                "usage_metadata": usage,
                                "runtime": runtime,
                            },
                        )
                        yield _make(last_msg.content, "response", node_name)

            except Exception as exc:  # noqa: BLE001
                # The exception is handled here and does not escape the span
                # context, so mark the span failed explicitly; otherwise trace
                # viewers show a failed turn as successful.
                span.record_exception(exc)
                span.set_status(
                    Status(StatusCode.ERROR, f"{type(exc).__name__}: {exc}")
                )
                logger.exception("AgentService error: %s: %s", type(exc).__name__, exc)
                yield _make(
                    _preview(f"{type(exc).__name__}: {exc!s}", self._preview_size),
                    "error",
                )
