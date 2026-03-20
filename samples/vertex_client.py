"""
Vertex AI Gemini client — single adapter for all LLM calls.

Representative sample from Financial Insights Hub.
Demonstrates the Adapter Pattern with Tenacity retry for transient GCP errors.

Patterns:
  - Adapter Pattern: Pipeline steps call GeminiClient, never the SDK directly.
    If Google changes the SDK, we update only this file.
  - Structured Output: response_mime_type="application/json"
  - Temperature = 0.0: Deterministic for financial data extraction
  - Token tracking: Every call returns usage metadata for cost dashboards
  - Tenacity retry: Exponential backoff on transient GCP errors only
  - OTel instrumentation: Every LLM call wrapped in GenAI semantic convention spans
"""

from __future__ import annotations

import json
import structlog
from collections.abc import AsyncGenerator
from typing import Any

import vertexai
from vertexai.generative_models import GenerativeModel, GenerationConfig, Part

from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)
from google.api_core.exceptions import (
    ResourceExhausted,
    ServiceUnavailable,
    InternalServerError,
    DeadlineExceeded,
)

from fin_insights.config import get_settings
from fin_insights.observability.spans import (
    get_tracer,
    llm_span,
    set_llm_response_attributes,
)
from fin_insights.observability.metrics import (
    LLM_TOKEN_COUNTER,
    LLM_DURATION_HISTOGRAM,
    LLM_CALL_COUNTER,
    LLM_TTFT_HISTOGRAM,
)

logger = structlog.get_logger(__name__)
_tracer = get_tracer(__name__)

# Only retry transient GCP errors — permanent errors (PermissionDenied,
# InvalidArgument, NotFound) will never succeed on retry.
_TRANSIENT_EXCEPTIONS = (
    ResourceExhausted,
    ServiceUnavailable,
    InternalServerError,
    DeadlineExceeded,
    TimeoutError,
    ConnectionError,
)


class GeminiClient:
    """Wraps Vertex AI Gemini — one instance per application.

    Provides typed methods for each LLM use case:
      - classify(): Document type classification
      - extract(): Financial data extraction from statements
      - generate_sql(): NLQ text-to-SQL generation
      - generate_answer(): RAG grounded answer generation
      - generate_answer_stream(): SSE streaming for chat
    """

    def __init__(self) -> None:
        settings = get_settings()
        vertexai.init(
            project=settings.gcp_project_id,
            location=settings.vertex_ai_location,
        )
        self._classification_model = GenerativeModel(settings.classification_model)
        self._extraction_model = GenerativeModel(settings.extraction_model)
        self._nlq_model = GenerativeModel(settings.nlq_model)
        self._extraction_max_tokens = settings.extraction_max_output_tokens
        self._classification_max_tokens = settings.classification_max_output_tokens
        self._nlq_max_tokens = settings.nlq_max_output_tokens
        self._rag_max_tokens = settings.rag_max_output_tokens
        self._agent_max_tokens = settings.agent_max_output_tokens
        self._llm_timeout = settings.llm_timeout_seconds
        self._extraction_timeout = settings.llm_extraction_timeout_seconds
        logger.info(
            "gemini_client_initialized",
            project=settings.gcp_project_id,
            location=settings.vertex_ai_location,
        )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type(_TRANSIENT_EXCEPTIONS),
        reraise=True,
    )
    async def classify(
        self,
        document_text: str,
        system_prompt: str,
        user_prompt: str,
        response_schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Classify a document using Gemini.

        Returns parsed JSON dict with doc_type + confidence.
        Retries up to 3x on transient GCP errors.
        """
        return await self._call_gemini(
            model=self._classification_model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            document_text=document_text,
            response_schema=response_schema,
            purpose="classification",
        )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type(_TRANSIENT_EXCEPTIONS),
        reraise=True,
    )
    async def extract(
        self,
        document_text: str,
        system_prompt: str,
        user_prompt: str,
        response_schema: dict[str, Any] | None = None,
        document_bytes: bytes | None = None,
        document_mime_type: str | None = None,
    ) -> dict[str, Any]:
        """Extract structured financial data from a document.

        If document_bytes is provided, sends the PDF/image as a multimodal
        Part so Gemini can see the actual table layout (column headers,
        AMOUNT vs BALANCE columns, etc.). Falls back to text-only if no bytes.
        """
        return await self._call_gemini(
            model=self._extraction_model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            document_text=document_text,
            response_schema=response_schema,
            purpose="extraction",
            document_bytes=document_bytes,
            document_mime_type=document_mime_type,
            max_output_tokens=self._extraction_max_tokens,
        )

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type(_TRANSIENT_EXCEPTIONS),
        reraise=True,
    )
    async def generate_sql(
        self,
        system_prompt: str,
        user_prompt: str,
    ) -> dict[str, Any]:
        """Generate SQL from a natural language question.

        Uses Gemini Pro for higher reasoning capability.
        Retries up to 2x on transient errors.
        """
        return await self._call_gemini(
            model=self._nlq_model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            purpose="nlq",
        )
