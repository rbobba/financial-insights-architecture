"""
Gemini document classifier — LLM-powered document type detection.

Representative sample from Financial Insights Hub ingestion pipeline.
Demonstrates the Two-Brain Pattern and Strategy Pattern for LLM integration.

Two-Brain Pattern (AI §11):
  Brain 1 (LLM): Reads document text, returns {"doc_type", "confidence", "reasoning"}
  Brain 2 (Python): Validates response, maps to DocType enum, clamps confidence, updates DB

Strategy Pattern:
  GeminiClassifier implements the same PipelineStep protocol as MockClassifier.
  The pipeline runner doesn't know which implementation is injected.
"""

from __future__ import annotations

import structlog
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from fin_insights.adapters.db.document import Document
from fin_insights.adapters.db.enums import DocType
from fin_insights.adapters.llm.vertex_client import GeminiClient
from fin_insights.adapters.llm.prompts.v1.classification import (
    SYSTEM_PROMPT,
    USER_PROMPT,
    RESPONSE_SCHEMA,
)
from fin_insights.pipeline.base import StepOutcome, StepResult

logger = structlog.get_logger(__name__)

# Map LLM string output → Python enum (Brain 2 validation)
DOC_TYPE_MAP: dict[str, DocType] = {dt.value: dt for dt in DocType}


class GeminiClassifier:
    """Classifies documents using Gemini — real LLM-powered classification."""

    def __init__(self, client: GeminiClient) -> None:
        self._client = client

    @property
    def step_name(self) -> str:
        return "classification"

    async def execute(
        self, document_id: UUID, session: AsyncSession
    ) -> StepResult:
        log = logger.bind(document_id=str(document_id))

        # 1. Load document with extracted text
        doc = await session.get(Document, document_id)
        if doc is None:
            return StepResult(
                outcome=StepOutcome.FAILED,
                error_detail="Document not found during classification",
            )

        if not doc.extracted_text:
            return StepResult(
                outcome=StepOutcome.FAILED,
                error_detail="No extracted text — text extraction must run first",
            )

        # 2. Send first ~8000 chars to Gemini for classification
        text_for_classification = doc.extracted_text[:8000]
        log.info("classification_started", text_length=len(text_for_classification))

        try:
            # 3. Brain 1: Call Gemini
            response = await self._client.classify(
                document_text=text_for_classification,
                system_prompt=SYSTEM_PROMPT,
                user_prompt=USER_PROMPT,
                response_schema=RESPONSE_SCHEMA,
            )

            llm_result = response["data"]
            usage = response["usage"]

            # 4. Brain 2: Validate LLM output
            raw_doc_type = llm_result.get("doc_type", "unknown")
            confidence = llm_result.get("confidence", 0.0)
            reasoning = llm_result.get("reasoning", "")

            # Map string to enum — fall back to UNKNOWN if invalid
            doc_type = DOC_TYPE_MAP.get(raw_doc_type, DocType.UNKNOWN)
            if doc_type == DocType.UNKNOWN and raw_doc_type != "unknown":
                log.warning("unknown_doc_type_from_llm", raw_value=raw_doc_type)

            # Clamp confidence to valid range [0, 1]
            confidence = max(0.0, min(1.0, float(confidence)))

            # 5. Update document record
            doc.doc_type = doc_type
            doc.doc_type_confidence = confidence
            await session.flush()

            log.info(
                "classification_completed",
                doc_type=doc_type.value,
                confidence=confidence,
                reasoning=reasoning,
                input_tokens=usage["input_tokens"],
                output_tokens=usage["output_tokens"],
            )

            return StepResult(
                outcome=StepOutcome.SUCCESS,
                output_summary={
                    "doc_type": doc_type.value,
                    "confidence": confidence,
                    "reasoning": reasoning,
                },
                model_used=usage["model"],
                tokens_input=usage["input_tokens"],
                tokens_output=usage["output_tokens"],
            )

        except Exception as e:
            log.exception("classification_failed")
            return StepResult(
                outcome=StepOutcome.FAILED,
                error_detail=f"Classification error: {str(e)}",
            )
