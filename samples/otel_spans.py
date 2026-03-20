"""
OpenTelemetry span helpers — context managers for LLM, RAG, and chat tracing.

Representative sample from Financial Insights Hub observability package.
Demonstrates GenAI semantic conventions (OpenTelemetry emerging standard)
for tracing Vertex AI / Gemini calls with structured span attributes.

Patterns:
  - Context manager spans with automatic duration tracking
  - GenAI semantic convention attributes (gen_ai.system, gen_ai.request.model, etc.)
  - Fail-safe error recording on span (set_status + record_exception)
  - Helper function for post-response token usage attributes
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Any, Generator

from opentelemetry import trace
from opentelemetry.trace import Span, StatusCode, Tracer


def get_tracer(name: str) -> Tracer:
    """Get a tracer for the given module.

    Wraps trace.get_tracer() with the application's instrumentation scope.
    If OTel is disabled (NoOp provider), returns a no-op tracer — zero overhead.
    """
    return trace.get_tracer(
        instrumenting_module_name=name,
        tracer_provider=trace.get_tracer_provider(),
    )


@contextmanager
def llm_span(
    tracer: Tracer,
    operation: str,
    *,
    model: str = "",
    temperature: float | None = None,
    max_tokens: int | None = None,
    purpose: str = "",
) -> Generator[Span, None, None]:
    """Context manager that creates an LLM span with GenAI semantic conventions.

    Usage:
        with llm_span(tracer, "generate_answer", model="gemini-2.5-flash") as span:
            result = await client.generate(...)
            span.set_attribute("gen_ai.usage.input_tokens", result.input_tokens)
            span.set_attribute("gen_ai.usage.output_tokens", result.output_tokens)
    """
    attributes: dict[str, Any] = {
        "gen_ai.system": "vertex_ai",
        "gen_ai.operation.name": operation,
    }
    if model:
        attributes["gen_ai.request.model"] = model
    if temperature is not None:
        attributes["gen_ai.request.temperature"] = temperature
    if max_tokens is not None:
        attributes["gen_ai.request.max_tokens"] = max_tokens
    if purpose:
        attributes["app.llm.purpose"] = purpose

    with tracer.start_as_current_span(
        name=f"gemini.{operation}",
        kind=trace.SpanKind.CLIENT,
        attributes=attributes,
    ) as span:
        start = time.perf_counter()
        try:
            yield span
            span.set_status(StatusCode.OK)
        except Exception as exc:
            span.set_status(StatusCode.ERROR, str(exc))
            span.record_exception(exc)
            raise
        finally:
            duration = time.perf_counter() - start
            span.set_attribute("gen_ai.client.duration_s", round(duration, 4))


@contextmanager
def rag_span(
    tracer: Tracer,
    operation: str,
    *,
    question: str = "",
) -> Generator[Span, None, None]:
    """Context manager for RAG pipeline spans.

    Usage:
        with rag_span(tracer, "answer", question="How much at Costco?") as span:
            result = await rag_service.answer(question)
            span.set_attribute("rag.context_chunks", len(results))
    """
    attributes: dict[str, Any] = {
        "rag.operation": operation,
    }
    if question:
        attributes["rag.question"] = question[:200]  # Truncate to avoid large attributes

    with tracer.start_as_current_span(
        name=f"rag.{operation}",
        kind=trace.SpanKind.INTERNAL,
        attributes=attributes,
    ) as span:
        start = time.perf_counter()
        try:
            yield span
            span.set_status(StatusCode.OK)
        except Exception as exc:
            span.set_status(StatusCode.ERROR, str(exc))
            span.record_exception(exc)
            raise
        finally:
            duration = time.perf_counter() - start
            span.set_attribute("rag.duration_s", round(duration, 4))


@contextmanager
def chat_span(
    tracer: Tracer,
    *,
    question: str = "",
    route: str = "",
) -> Generator[Span, None, None]:
    """Context manager for top-level chat request spans.

    Creates the root business span for a chat request.
    Child spans (classify, retrieve, generate) nest under this.
    """
    attributes: dict[str, Any] = {}
    if question:
        attributes["chat.question"] = question[:200]
    if route:
        attributes["chat.route"] = route

    with tracer.start_as_current_span(
        name="chat.process",
        kind=trace.SpanKind.INTERNAL,
        attributes=attributes,
    ) as span:
        start = time.perf_counter()
        try:
            yield span
            span.set_status(StatusCode.OK)
        except Exception as exc:
            span.set_status(StatusCode.ERROR, str(exc))
            span.record_exception(exc)
            raise
        finally:
            duration = time.perf_counter() - start
            span.set_attribute("chat.duration_s", round(duration, 4))


def set_llm_response_attributes(
    span: Span,
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    finish_reason: str = "",
    model: str = "",
) -> None:
    """Set standard GenAI response attributes on a span.

    Call this after receiving the LLM response to record token usage
    and finish reason. These attributes power cost dashboards in
    Prometheus / Grafana.
    """
    span.set_attribute("gen_ai.usage.input_tokens", input_tokens)
    span.set_attribute("gen_ai.usage.output_tokens", output_tokens)
    span.set_attribute("gen_ai.usage.total_tokens", input_tokens + output_tokens)
    if finish_reason:
        span.set_attribute("gen_ai.response.finish_reason", finish_reason)
    if model:
        span.set_attribute("gen_ai.response.model", model)
