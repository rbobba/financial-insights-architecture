# Document Upload & Processing Pipeline

> **Financial Insights Hub** · FastAPI · Celery · Gemini 2.5 · pgvector

---

## Pipeline Overview

```
┌─────────┐    ┌──────────┐    ┌──────────────┐    ┌───────────────┐    ┌──────────────┐    ┌────────────┐
│ Upload  │───►│ Validate │───►│  Classify    │───►│  Extract      │───►│  Validate &  │───►│ Normalize  │
│ (API)   │    │ & Store  │    │  (Gemini     │    │  (Gemini      │    │  Reconcile   │    │ & Store    │
│         │    │          │    │   Flash)     │    │   Flash)      │    │  (Python)    │    │ (DB)       │
└─────────┘    └──────────┘    └──────────────┘    └───────────────┘    └──────────────┘    └────────────┘
   HTTP           SHA-256         Two-Brain           Two-Brain          Deterministic       Pydantic →
   POST           dedup           Pattern             Pattern            math checks         SQLAlchemy
   202            GCS store       Brain1: LLM         Brain1: LLM        Brain2: Python      Embeddings
                                  Brain2: Python      Brain2: Python                         generated
```

---

## 1. Upload API Contract

### Preflight Check
```
POST /api/v1/documents/preflight
Content-Type: application/json

{
  "filename": "statement_oct_2025.pdf",
  "file_size": 2450000,
  "file_hash": "sha256:a1b2c3..."
}

Response 200 (OK):
{
  "allowed": true,
  "is_duplicate": false,
  "max_size_bytes": 20971520    // 20 MB
}

Response 200 (Duplicate):
{
  "allowed": false,
  "is_duplicate": true,
  "existing_document_id": "uuid",
  "message": "Document with this hash already exists"
}
```

### Upload
```
POST /api/v1/documents/upload
Content-Type: multipart/form-data

Response 202 (Accepted):
{
  "document_id": "uuid",
  "status": "uploaded",
  "message": "Document queued for processing"
}
```

### Status Polling
```
GET /api/v1/documents/{id}/status

Response 200:
{
  "document_id": "uuid",
  "status": "extracting",
  "steps": [
    {"name": "classification", "status": "completed", "duration_ms": 1200},
    {"name": "text_extraction", "status": "completed", "duration_ms": 800},
    {"name": "llm_extraction", "status": "in_progress", "started_at": "..."}
  ]
}
```

---

## 2. Three-Layer Validation Architecture

| Layer | Type | What It Checks | Failure Action |
|-------|------|---------------|----------------|
| **Layer 1** | File validation (deterministic) | MIME type, file size, magic bytes, virus scan | Reject immediately (400) |
| **Layer 2** | Duplicate detection (deterministic) | SHA-256 content hash | Return existing doc (409) |
| **Layer 3** | Business validation (after extraction) | Reconciliation, confidence, completeness | Flag for review |

---

## 3. Processing Pipeline Steps

### Step 1: Text Extraction
- PDF: `pypdf` for native text; Document AI OCR for scanned documents
- Auto-detection: If `pypdf` extracts < 100 characters, fall back to OCR
- Output: extracted text + per-page text array

### Step 2: Classification (Two-Brain Pattern)
- **Brain 1 (LLM)**: Gemini Flash reads first 8,000 chars → returns `{doc_type, confidence, reasoning}`
- **Brain 2 (Python)**: Maps string to `DocType` enum, clamps confidence to [0,1], validates against known types
- Falls back to `unknown` with low confidence if model output is unexpected

### Step 3: LLM Extraction
- **Brain 1 (LLM)**: Gemini Flash processes full document with type-specific prompt → returns structured JSON per extraction contract
- **Brain 2 (Python)**: JSON Schema validation, field-level type checking, PII redaction (mask account numbers)
- For native PDF: sends raw PDF bytes as multimodal input (preserves table layout)
- `temperature=0.0` for deterministic extraction

### Step 4: Validation & Reconciliation
- JSON Schema validation against extraction contract
- Reconciliation math (statement totals must balance within ±$0.02)
- Confidence assessment (per-field confidence from LLM)
- Self-healing: On validation failure, retry with error feedback in prompt (max 1 retry)

### Step 5: Normalization & Storage
- Map extracted JSON to database entities (Party, Account, Transaction, LineItem)
- Entity resolution: Match extracted merchants to existing `party` records using trigram similarity + embeddings
- Generate 768-dimensional embeddings for semantic search
- Write to PostgreSQL in a single transaction

---

## 4. Confidence Scoring & Human-in-the-Loop

| Confidence | Classification | Action |
|------------|---------------|--------|
| ≥ 0.90 | High | Auto-accept; store directly |
| 0.70 – 0.89 | Medium | Accept with warnings; highlight uncertain fields in UI |
| 0.50 – 0.69 | Low | Flag for human review; show side-by-side with PDF |
| < 0.50 | Very Low | Mark as `needs_review`; do not auto-categorize transactions |

---

## 5. Performance Requirements

| Metric | Target |
|--------|--------|
| Upload to "processing started" | < 2 seconds |
| Classification latency | < 3 seconds |
| Full extraction (1-page receipt) | < 10 seconds |
| Full extraction (10-page statement) | < 60 seconds |
| Full extraction (30-page brokerage) | < 90 seconds |
| Throughput (concurrent) | 5 documents simultaneously |

---

## 6. Security

| Concern | Mitigation |
|---------|-----------|
| File upload attacks | MIME type validation, magic byte check, 20 MB limit |
| PII in PDF | Account numbers masked in extraction; never stored in plaintext |
| Storage | AES-256 encryption at rest (GCS default) |
| Processing | Celery worker runs with least-privilege DB credentials |
| Audit trail | Complete processing log with per-step timing and cost |
