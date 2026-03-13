# LLM Extraction Contract — Schema, Prompts & Validation

> **Financial Insights Hub** · Google Vertex AI · Gemini 2.5 Flash/Pro · Structured Output

---

## 1. Extraction Pipeline Overview

```
PDF Upload
    │
    ▼
┌──────────────────┐     ┌──────────────────┐     ┌──────────────────┐
│  Classification  │────►│  OCR / Text      │────►│  LLM Extraction  │
│  (Gemini Flash)  │     │  Extraction      │     │  (Gemini Flash)  │
│                  │     │                  │     │                  │
│  Output:         │     │  Output:         │     │  Output:         │
│  doc_type +      │     │  raw text        │     │  Structured JSON │
│  confidence      │     │                  │     │  per contract    │
└──────────────────┘     └──────────────────┘     └────────┬─────────┘
                                                           │
                         ┌──────────────────┐     ┌────────▼─────────┐
                         │  Normalization   │◄────│  Validation &    │
                         │  & Storage       │     │  Reconciliation  │
                         │                  │     │  (Deterministic) │
                         │  Maps to DB      │     │  Math checks     │
                         │  schema          │     │  Confidence      │
                         └──────────────────┘     └──────────────────┘
```

---

## 2. Classification Contract

### Prompt
```
You are a financial document classifier. Given the first 2 pages of text
from a PDF, determine the document type.

Respond with ONLY a JSON object:
{
  "doc_type": "<credit_card_statement|bank_statement|invoice|receipt|utility_bill|tax_document|unknown>",
  "confidence": <float 0.0 to 1.0>,
  "reasoning": "<1-2 sentence explanation>",
  "detected_issuer": "<company/bank name if identifiable, else null>",
  "detected_date": "<YYYY-MM-DD or null>"
}

Rules:
- If you cannot determine the type with > 0.5 confidence, use "unknown"
- Do NOT guess. If ambiguous, lower confidence accordingly.
```

### Model Selection
| Model | Cost/Call | Latency | Use When |
|-------|----------|---------|----------|
| Gemini 2.5 Flash | ~$0.0005 | < 2s | Default — fast and cheap |
| Gemini 2.5 Pro | ~$0.005 | < 5s | Fallback if Flash confidence < 0.7 |

---

## 3. Extraction Contract — JSON Schema

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "FinancialDocumentExtraction",
  "type": "object",
  "required": ["schema_version", "doc_type", "document", "parties", "confidence"],
  "properties": {
    "schema_version": { "type": "string", "const": "1.0.0" },
    "doc_type": {
      "type": "string",
      "enum": ["credit_card_statement", "bank_statement", "invoice", "receipt", "utility_bill"]
    },
    "document": { "$ref": "#/definitions/DocumentMeta" },
    "parties": { "type": "array", "items": { "$ref": "#/definitions/Party" }, "minItems": 1 },
    "accounts": { "type": "array", "items": { "$ref": "#/definitions/Account" } },
    "statement_cycle": { "$ref": "#/definitions/StatementCycle" },
    "transactions": { "type": "array", "items": { "$ref": "#/definitions/Transaction" } },
    "line_items": { "type": "array", "items": { "$ref": "#/definitions/LineItem" } },
    "totals": { "$ref": "#/definitions/Totals" },
    "warnings": { "type": "array", "items": { "type": "string" } },
    "confidence": { "$ref": "#/definitions/ConfidenceBlock" }
  }
}
```

The full schema defines `Party`, `Account`, `StatementCycle`, `Transaction`, `LineItem`, `Totals`, and `ConfidenceBlock` types with strict constraints — dates in YYYY-MM-DD format, confidence as 0.0–1.0 floats, masked account numbers, and transaction type enums.

---

## 4. Extraction Prompts (Per Document Type)

### Credit Card Statement
```
You are a financial document extraction expert. Extract structured data from
this credit card statement.

RULES (CRITICAL):
1. Output ONLY valid JSON matching the schema. No markdown, no explanation.
2. If a field is not found, set to null. NEVER guess or fabricate values.
3. Amounts: Positive for charges/debits. Negative for payments/credits.
4. Account numbers: Mask all but last 4 digits (e.g., "xxxx-xxxx-xxxx-1234").
5. Extract EVERY transaction. Do not skip any.
6. Use EXACT text from the statement for descriptions. Do not paraphrase.
7. Set confidence (0.0–1.0) per transaction.
8. Reconciliation: sum of transactions ≈ (new_balance - previous_balance + payments).
   Note any mismatch in warnings.
```

Similar extraction prompts exist for bank statements, receipts/invoices, and utility bills — each tuned to the specific document format with appropriate reconciliation rules.

---

## 5. Validation & Reconciliation

### Validation Pipeline
```python
class ValidationService:
    """Deterministic checks on LLM extraction output (Brain 2)."""

    def validate(self, extraction: dict, doc_type: str) -> ValidationResult:
        errors, warnings = [], []

        # Level 1: JSON Schema validation
        errors.extend(self._validate_schema(extraction))

        # Level 2: Type-specific business rules
        if doc_type == 'credit_card_statement':
            errors.extend(self._validate_statement(extraction))
        elif doc_type in ('receipt', 'invoice'):
            errors.extend(self._validate_receipt(extraction))

        # Level 3: Cross-field consistency
        warnings.extend(self._check_consistency(extraction))

        # Level 4: Reconciliation math
        recon = self._reconcile(extraction, doc_type)
        if not recon.passed:
            warnings.append(recon.message)

        return ValidationResult(
            is_valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
            reconciled=recon.passed,
            reconciliation_diff=recon.diff,
        )
```

### Reconciliation Rules

| Document Type | Rule | Tolerance |
|--------------|------|-----------|
| **Credit Card** | `previous_balance - payments + purchases + fees + interest ≈ new_balance` | ±$0.02 |
| **Receipt** | `sum(line_items.total_price) ≈ subtotal`; `subtotal + tax - discount ≈ total` | ±$0.01 |
| **Bank Statement** | `opening_balance + credits - debits ≈ closing_balance` | ±$0.02 |

### Validation Error Classes

| Error Type | Severity | Action |
|-----------|----------|--------|
| `SCHEMA_INVALID` | Error | Reject; re-extract |
| `DATE_INVALID` | Error | Set to null; flag for review |
| `AMOUNT_MISMATCH` | Warning | Store; flag for human review |
| `MISSING_TRANSACTIONS` | Warning | Flag for review |
| `ACCOUNT_EXPOSED` | Error | Redact immediately |
| `CONFIDENCE_LOW` | Warning | Flag entire document for review |

---

## 6. Structured Output Mode (Gemini)

```python
from vertexai.generative_models import GenerativeModel, GenerationConfig

class VertexExtractionClient:
    def __init__(self, model_name: str = "gemini-2.5-flash"):
        self.model = GenerativeModel(model_name)

    async def extract(self, text: str, doc_type: str) -> dict:
        config = GenerationConfig(
            temperature=0.0,                           # Deterministic output
            top_p=1.0,
            max_output_tokens=8192,
            response_mime_type="application/json",     # Force JSON
            response_schema=self._get_schema(doc_type) # Enforce schema
        )
        response = await self.model.generate_content_async(
            self._build_prompt(text, doc_type),
            generation_config=config,
        )
        return json.loads(response.text)
```

**Key insight**: Extraction is a **deterministic task** — we want the same output every time for the same input. `temperature=0.0` eliminates sampling randomness.

---

## 7. Prompt Engineering Strategy

### Prompt Structure
```
┌──────────────────────────────────────────┐
│ SYSTEM: Role definition + rules          │  ← Fixed per doc_type
├──────────────────────────────────────────┤
│ DOCUMENT TEXT: {extracted_text}           │  ← Variable (from OCR/text)
├──────────────────────────────────────────┤
│ OUTPUT SCHEMA: {json_schema}             │  ← Fixed per schema_version
├──────────────────────────────────────────┤
│ FEW-SHOT EXAMPLES (optional):            │  ← 1-2 examples for complex formats
└──────────────────────────────────────────┘
```

### Prompt Versioning
- Templates stored in `adapters/llm/prompts/` (version-controlled in git)
- Each extraction records `extraction_version` in the `document` table
- Old extractions retain their version for re-evaluation
- Allows re-processing documents with new prompt versions without migration

---

## 8. Error Recovery & Self-Healing

| Failure | Detection | Recovery |
|---------|-----------|----------|
| **Invalid JSON** | Parse error | Retry with `response_mime_type=json` |
| **Schema violation** | JSON Schema validation fails | Retry with error feedback in prompt |
| **Truncated output** | Output ends mid-JSON | Increase `max_output_tokens`; retry |
| **Hallucinated data** | Reconciliation fails | Flag for human review |
| **Missing transactions** | Count mismatch | Retry with "you missed N transactions" prompt |
| **Rate limit** (429) | HTTP 429 | Exponential backoff; max 3 retries |

### Self-Healing Prompt (Retry with Error Context)
```
Your previous extraction had the following errors:
{validation_errors}

Please re-extract the data, fixing these specific issues.
Original document text: {document_text}
```

---

## 9. Cost Estimation

| Document Type | Avg Input Tokens | Avg Output Tokens | Flash Cost | Pro Cost |
|--------------|-----------------|-------------------|------------|----------|
| Credit Card Statement | 3,000–5,000 | 1,500–3,000 | ~$0.002 | ~$0.02 |
| Bank Statement | 2,000–3,000 | 1,000–2,000 | ~$0.001 | ~$0.01 |
| Receipt | 500–1,500 | 300–800 | ~$0.0005 | ~$0.005 |
| Invoice | 800–2,000 | 500–1,500 | ~$0.0008 | ~$0.008 |
| Utility Bill | 1,500–2,500 | 500–1,000 | ~$0.001 | ~$0.01 |

**Strategy**: Use Flash for all types by default. Upgrade to Pro only for documents that fail validation (~10% of cases). This saves ~90% on LLM costs.
