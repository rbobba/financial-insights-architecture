# Feedback Loop & Accuracy Roadmap

> **Financial Insights Hub** · 6 Feedback Loops · Human-in-the-Loop · Continuous Accuracy Improvement

---

## 1. Feedback Loop Architecture

```
┌───────────────────────────────────────────────────────────────────────────┐
│                           FEEDBACK LOOPS                                  │
│                                                                           │
│  Loop 1: Correction → Auto-Rule                                          │
│  User corrects category → merchant_category_rule auto-created            │
│  Future extractions for same merchant → deterministic (no LLM needed)    │
│                                                                           │
│  Loop 2: Extraction Error → Prompt Refinement                            │
│  Repeated extraction errors → logged → prompt templates updated          │
│  Eval suite catches regressions before deployment                        │
│                                                                           │
│  Loop 3: NLQ Feedback → Few-Shot Learning                                │
│  User 👍 → question+SQL added to golden examples                        │
│  User 👎 → flagged for review, correct SQL logged                       │
│  Dynamic few-shot selects from growing golden set                        │
│                                                                           │
│  Loop 4: RAG Feedback → Retrieval Quality Tuning                         │
│  User rates RAG answers → diagnose retrieval vs generation failures      │
│  Tune similarity threshold, chunk strategy, re-ranking                   │
│                                                                           │
│  Loop 5: Agent Self-Evaluation                                            │
│  Agent logs reasoning trace → self-critique → confidence score           │
│  Low-confidence answers flagged for human review                         │
│                                                                           │
│  Loop 6: Extraction Confidence → Review Queue                            │
│  Low-confidence transactions → human review queue                        │
│  Reviewed transactions → training data for eval suite                    │
│                                                                           │
└───────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Loop 1: Correction → Auto-Rule Pipeline (Implemented)

### Flow

```
User clicks category cell → CategoryCorrectionDialog opens
User selects new category → PATCH /api/v1/transactions/{id}/category
→ CorrectionService.correct_category() executes 5-step pipeline:
  │
  ├── Step 1: Load transaction + old category
  ├── Step 2: Update transaction.category_id
  ├── Step 3: Insert correction audit record
  ├── Step 4: Auto-create/upsert merchant_category_rule
  │           Pattern: ILIKE extracted from merchant name
  │           Priority: 10, Source: user_correction, Confidence: 1.00
  └── Step 5: Return response with rule metadata

On next document upload → reconciliation pipeline runs:
  → TenantMerchantOverrideRule (runs FIRST, position 1)
  → Loads all merchant_category_rules for current user
  → Matched transactions get deterministic category — LLM bypassed
```

### Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| Immediate rule creation (not threshold-based) | Every user correction is authoritative — single correction carries high confidence |
| Upsert, not insert | Same pattern re-corrected → existing rule updated, `times_applied` tracks usage |
| ILIKE patterns | `%merchant_name%` for PostgreSQL compatibility rather than glob patterns |
| Multi-tenant via RLS | Each rule scoped to `user_id`, RLS enforces tenant isolation |

---

## 3. Loop 2: Extraction Error → Prompt Refinement

### Error Classification

| Error Type | Example | Detection |
|-----------|---------|-----------|
| Missing transaction | 20 in PDF, 18 extracted | Count mismatch in reconciliation |
| Wrong amount | $42.50 → $425.00 | Sum mismatch in reconciliation |
| Wrong category | Payment classified as debit | Human correction |
| Wrong merchant | Issuer name instead of merchant | Normalization rules |
| Hallucinated transaction | Non-existent in source PDF | Human deletion |

### Prompt Improvement Workflow

```
Monthly review cycle:
  1. Run error_tracker.get_error_summary()
  2. Identify top error categories by issuer and field
  3. Update extraction prompt templates:
     - Add explicit rules for recurring errors
     - Add negative examples ("DO NOT classify X as Y")
  4. Run eval suite against golden dataset
  5. If accuracy improves → commit → deploy
  6. If accuracy regresses → rollback → investigate
```

### Golden Dataset for Extraction

```sql
CREATE TABLE finance.golden_extraction (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID NOT NULL REFERENCES finance.document(id),
    expected_json JSONB NOT NULL,
    verified_by UUID REFERENCES finance.app_user(id),
    verified_at TIMESTAMPTZ DEFAULT now(),
    version INT DEFAULT 1,
    notes TEXT
);
```

---

## 4. Loop 3: NLQ Feedback → Few-Shot Learning

### Target Flow

```
User asks: "How much did I spend on dining in December?"
→ NLQ generates SQL → executes → returns result
→ User clicks 👍 (correct answer)
→ System saves to golden NLQ examples:
   { question, sql, question_embedding[768] }
→ Next similar question:
   dynamic_few_shot.py finds this example via embedding similarity
   → Includes it as a few-shot example in the NLQ prompt
   → LLM generates better SQL with a proven example
```

### Few-Shot Source Ranking

```python
class DynamicFewShotProvider:
    """Selects few-shot examples from a growing pool of verified Q&A pairs."""

    async def get_examples(self, question: str, limit: int = 5) -> list[FewShotExample]:
        """
        Source ranking:
        1. User-verified positive feedback (highest trust)
        2. Admin-curated golden examples
        3. Static seed examples (fallback)
        """
        question_embedding = await self._embed(question)
        examples = await self._session.execute(
            select(NlqGoldenExample)
            .where(NlqGoldenExample.is_active == True)
            .order_by(
                NlqGoldenExample.question_embedding.cosine_distance(question_embedding)
            )
            .limit(limit)
        )
        results = examples.scalars().all()
        if len(results) < limit:
            results.extend(self._get_static_examples(limit - len(results)))
        return results
```

---

## 5. Loop 4: RAG Quality Scoring

### Failure Mode Diagnosis

| Failure Mode | Root Cause | Detection |
|-------------|-----------|-----------|
| Retrieval failure | Right docs not in top-K | Good question, irrelevant sources |
| Generation failure | Right docs retrieved, wrong answer | Relevant sources, incorrect synthesis |
| Embedding quality | Similar concepts → distant vectors | Consistently low retrieval scores |
| Chunk boundary | Answer spans two chunks | Partial answers |

### Ragas-Style Evaluation Metrics

- **Context Relevancy**: Are retrieved chunks relevant to the question?
- **Faithfulness**: Does the answer only use information from the context?
- **Answer Relevancy**: Does the answer address the question?
- **Answer Correctness**: (Requires ground truth) Is the answer factually correct?

---

## 6. Loop 5: Agent Self-Evaluation

```python
class AgentOrchestrator:

    async def _self_evaluate(self, question: str, result: AgentResult) -> SelfEvaluation:
        """Ask LLM to critique its own reasoning and answer."""
        eval_prompt = f"""
        You just answered: {question}
        Your reasoning: {result.steps}
        Your answer: {result.answer}

        Self-evaluate:
        1. Confidence (0-100)
        2. Data quality: enough data to answer fully?
        3. Assumptions made
        4. Limitations or incompleteness
        5. Suggested follow-up question
        """
        eval_result = await self._gemini_client.generate_answer(
            system_prompt="You are a financial AI quality auditor.",
            user_prompt=eval_prompt,
        )
        return SelfEvaluation.from_json(eval_result)
```

Low-confidence answers (< 50) are routed to a human review queue via a database view.

---

## 7. Loop 6: Confidence-Based Review Queue

### Review Triggers

| Trigger | Threshold |
|---------|-----------|
| Transaction confidence | Below `medium` |
| Reconciliation mismatch | Sum or count discrepancy |
| Agent self-evaluation | Confidence < 50 |
| Duplicate transaction candidate | Similarity score > 0.95 |

### Review Queue UI

```
┌────────────────────────────────────────────────────────────────┐
│  Review Queue (12 items pending)                                │
├────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ⚠ Transaction: "AMZN MKTP US" — $47.82                        │
│    Category: Shopping (confidence: low)                          │
│    [Confirm ✓] [Change Category ✏️] [Delete ✗]                  │
│                                                                  │
│  ⚠ Document: "chase_jan2026.pdf"                                │
│    Issue: Statement total mismatch ($48 gap)                    │
│    [Review Transactions] [Mark as OK] [Re-extract]              │
│                                                                  │
│  ⚠ NLQ Answer: "What's my net income?"                          │
│    Confidence: 35/100 — "Could not find payroll data"           │
│    [View Answer] [Mark Correct] [Mark Wrong]                    │
│                                                                  │
└────────────────────────────────────────────────────────────────┘
```

---

## 8. Quality Dashboard

| Metric Group | Key Metrics |
|-------------|-------------|
| **Extraction** | Accuracy %, avg confidence, correction count, auto-rules created |
| **NLQ** | Positive feedback %, negative %, golden examples count |
| **RAG** | Avg user rating, retrieval accuracy, faithfulness score |
| **Agent** | Avg self-eval confidence, tool calls per query, timeout rate |
| **Review Queue** | Pending items, completion rate, monthly correction trend |

---

## 9. RAG Enhancement Roadmap

| Enhancement | Effort | Impact | Priority |
|------------|:------:|:------:|:--------:|
| Hybrid search (semantic + keyword via pg_trgm) | 2 days | Medium | P0 |
| Re-ranking with LLM | 1 day | High | P0 |
| Query expansion (multi-variant rewrites) | 1 day | Medium | P1 |
| Self-consistency verification | 2 days | Medium | P1 |
| Context compression | 2 days | Low | P2 |
| Multi-index (per entity type) | 3 days | Medium | P2 |

---

## 10. Implementation Phases

| Phase | Component | Effort | Impact |
|-------|-----------|:------:|:------:|
| 14A | Correction → auto-rule pipeline | 2-3 days | High |
| 14B | NLQ feedback → golden examples + few-shot | 3-4 days | High |
| 14C | Review queue (UI + backend) | 3-4 days | High |
| 14D | Extraction error tracking + golden dataset | 2-3 days | Medium |
| 14E | RAG hybrid search + re-ranking | 3-4 days | High |
| 14F | Agent self-evaluation | 2-3 days | Medium |
| 14G | RAG evaluation (Ragas-style) | 3-4 days | Medium |
| 14H | Quality dashboard | 3-4 days | Medium |
| 14I | Agent planning + memory | 4-5 days | Medium |

**Accuracy-First Principle**: For financial data, **correctness > speed > breadth**. The system never hallucinates numbers — if unsure, it says "I don't have that data" and shows provenance for every calculation.
