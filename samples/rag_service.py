"""
RAG Service — retrieves context via pgvector and generates grounded answers.

Representative sample from Financial Insights Hub.
Demonstrates the RAG orchestration pattern: embed → search → format → generate.

Patterns:
  - Frozen dataclasses for immutable result objects
  - Dependency injection (SearchService, GeminiClient)
  - OTel span instrumentation on the RAG pipeline
  - Prometheus metrics for search duration and context size
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import structlog

from fin_insights.adapters.llm.prompts.v1.rag import build_rag_prompt
from fin_insights.services.search_service import SearchService, SearchResultItem
from fin_insights.observability.spans import get_tracer, rag_span
from fin_insights.observability.metrics import RAG_CONTEXT_SIZE, RAG_SEARCH_DURATION

logger = structlog.get_logger(__name__)
_tracer = get_tracer(__name__)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Result Data Objects
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@dataclass(frozen=True)
class SourceReference:
    """A source cited in the RAG answer — for frontend linking."""

    entity_type: str    # "transaction" | "document" | "party"
    entity_id: UUID
    title: str
    score: float


@dataclass
class RagResult:
    """Complete result from the RAG pipeline."""

    question: str
    answer: str
    sources: list[SourceReference] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# RAG Service
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class RagService:
    """Orchestrates the RAG pipeline: question → context → answer.

    Pipeline:
      1. Embed question → 768-dim vector (EmbeddingService)
      2. Semantic search → top-K ranked results (SearchService)
      3. Format results into context blocks
      4. Build RAG prompt with context + question
      5. Call GeminiClient.generate_answer() → grounded text
      6. Return RagResult with answer, source references, usage metadata

    Dependencies (injected):
        search_service: Embeds queries and runs semantic search.
        gemini_client:  Generates grounded text answers.
    """

    _SEARCH_LIMIT: int = 10
    _SEARCH_THRESHOLD: float = 0.6

    def __init__(
        self,
        search_service: SearchService,
        gemini_client: Any,
    ) -> None:
        self._search = search_service
        self._client = gemini_client

    async def answer(self, question: str) -> RagResult:
        """Run the full RAG pipeline for a user question.

        Args:
            question: The user's natural-language question.

        Returns:
            RagResult with grounded answer, source references, and token usage.
        """
        log = logger.bind(question=question[:80])
        log.info("rag_pipeline_started")

        with rag_span(_tracer, "answer", question=question) as span:
            try:
                # Step 1+2: Embed question + semantic search
                import time as _time
                _search_start = _time.perf_counter()
                search_results = await self._search.semantic_search(
                    query=question,
                    entity_types=None,
                    limit=self._SEARCH_LIMIT,
                    threshold=self._SEARCH_THRESHOLD,
                )
                _search_dur = _time.perf_counter() - _search_start
                RAG_SEARCH_DURATION.record(_search_dur, {"rag.operation": "answer"})

                total_results = search_results.total_results
                span.set_attribute("rag.search.total_results", total_results)
                RAG_CONTEXT_SIZE.record(total_results, {"rag.operation": "answer"})

                # Step 3+4: Convert results to dicts, build prompt
                result_dicts = self._results_to_dicts(search_results.results)
                system_prompt, user_prompt = build_rag_prompt(
                    question=question,
                    search_results=result_dicts,
                )

                # Step 5: Call LLM to generate grounded answer
                llm_result = await self._client.generate_answer(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                )

                answer = llm_result.get("answer", "")
                usage = llm_result.get("usage", {})

                # Step 6: Build source references from search results
                sources = self._build_sources(search_results.results)

                log.info(
                    "rag_pipeline_completed",
                    answer_length=len(answer),
                    source_count=len(sources),
                )

                return RagResult(
                    question=question,
                    answer=answer,
                    sources=sources,
                    usage=usage,
                )
            except Exception:
                log.exception("rag_pipeline_failed")
                return RagResult(
                    question=question,
                    answer="I wasn't able to find a good answer. Please try rephrasing.",
                )
