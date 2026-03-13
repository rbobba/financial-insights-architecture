"""
NLQ Service — orchestrates the natural language to SQL pipeline.

Representative sample from Financial Insights Hub.
Demonstrates the 10-step NLQ pipeline with self-healing retry,
dynamic few-shot learning, and entity resolution.

Pipeline flow:
    1. sanitize_question()          → clean & validate user input
    2. build_schema_context()       → fetch live categories + accounts from DB
   2b. entity_resolver.resolve()    → ground entities to exact DB names
   2c. dynamic_few_shot.get_examples() → retrieve similar past Q&A pairs
    3. build_nlq_prompt()           → assemble system + user prompts
    4. gemini_client.generate_sql() → call Gemini Pro for SQL generation
    5. strip_sql_response()         → clean LLM output (markdown fences, etc.)
    6. validate_sql_safety()        → multi-layer security check
    7. nlq_repo.execute_readonly()  → execute with timeout + row limit
    8. infer_chart_hint()           → suggest visualization type
    9. log_query()                  → store Q&A pair for future learning
   10. return NlqResult             → question, sql, data, chart_hint, usage

Self-healing: On SQL execution error, retries once with the error message
fed back to the LLM so it can fix its own mistake. Never retries security
violations (UnsafeSQLError).
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from fin_insights.adapters.llm.vertex_client import GeminiClient
from fin_insights.adapters.llm.embedding_service import EmbeddingService
from fin_insights.adapters.llm.prompts.v1.nlq import build_nlq_prompt
from fin_insights.services.dynamic_few_shot import DynamicFewShotProvider
from fin_insights.services.entity_resolver import EntityResolver, format_entity_context
from fin_insights.services.nlq_validators import (
    sanitize_question,
    strip_sql_response,
    validate_sql_safety,
    validate_sql_completeness,
    detect_llm_refusal,
    infer_chart_hint,
)
from fin_insights.shared.exceptions import NlqError, UnsafeSQLError

logger = structlog.get_logger(__name__)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Result Data Objects
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@dataclass
class NlqResult:
    """Complete NLQ response returned to the API layer."""

    question: str
    sql: str
    columns: list[str] = field(default_factory=list)
    rows: list[dict] = field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    chart_hint: str | None = None
    answer: str | None = None       # Friendly text (refusal, summary, etc.)
    usage: dict[str, Any] = field(default_factory=dict)
    retried: bool = False           # True if self-healing retry was used
    query_log_id: UUID | None = None  # For feedback correlation


@dataclass
class SchemaContext:
    """Dynamic context fetched from DB for prompt injection.

    Injected into the NLQ prompt so the LLM knows the actual
    category names, account names, and industry values in the database.
    """

    categories: list[str] = field(default_factory=list)
    accounts: list[dict[str, str]] = field(default_factory=list)
    industries: list[str] = field(default_factory=list)
