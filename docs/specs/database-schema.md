# Database Schema — DDL, Data Types, Constraints & Validation

> **Financial Insights Hub** · PostgreSQL 16 · pgvector · Alembic

---

## 1. Design Principles

| Principle | Description |
|-----------|-------------|
| **Schema namespace** | All tables under `finance` schema — explicit namespace isolation |
| **UUID primary keys** | `uuid` type with `gen_random_uuid()` default — distributed-safe |
| **Timestamps everywhere** | `created_at`, `updated_at` on all tables with timezone (`TIMESTAMPTZ`) |
| **Soft deletes** | `deleted_at` nullable timestamp (not boolean) — enables "when was it deleted?" |
| **JSONB for flexibility** | Vendor-specific fields in JSONB columns — indexable, containment queries |
| **Enums as Postgres enums** | `CREATE TYPE` for fixed value sets — type-safe, compact storage |
| **Referential integrity** | Foreign keys with explicit `ON DELETE` policies |
| **pgvector extension** | Vector embeddings for semantic search — single-DB architecture |

---

## 2. PostgreSQL Extensions

```sql
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";      -- UUID generation (fallback)
CREATE EXTENSION IF NOT EXISTS "pgcrypto";        -- gen_random_uuid() (preferred)
CREATE EXTENSION IF NOT EXISTS "vector";          -- pgvector for embeddings
CREATE EXTENSION IF NOT EXISTS "pg_trgm";         -- Trigram similarity for fuzzy search
CREATE EXTENSION IF NOT EXISTS "btree_gin";       -- GIN index support for btree types
```

---

## 3. Enum Types

```sql
CREATE TYPE finance.doc_type AS ENUM (
    'credit_card_statement', 'bank_statement', 'invoice',
    'receipt', 'utility_bill', 'tax_document', 'unknown'
);

CREATE TYPE finance.processing_status AS ENUM (
    'uploaded', 'classifying', 'classified', 'ocr_processing', 'ocr_complete',
    'extracting', 'extracted', 'validating', 'validated',
    'failed', 'needs_review', 'corrected'
);

CREATE TYPE finance.transaction_type AS ENUM (
    'debit', 'credit', 'payment', 'refund', 'fee',
    'interest', 'transfer', 'reward', 'adjustment'
);

CREATE TYPE finance.party_role AS ENUM ('issuer', 'merchant', 'payee', 'payer');

CREATE TYPE finance.confidence_level AS ENUM ('high', 'medium', 'low', 'very_low');
```

---

## 4. Core Tables (Selected DDL)

### 4.1 `finance.document` — Source Document Metadata

```sql
CREATE TABLE finance.document (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    original_filename   VARCHAR(500) NOT NULL,
    storage_path        VARCHAR(1000) NOT NULL,
    file_hash           VARCHAR(64) NOT NULL UNIQUE,     -- SHA-256 for deduplication
    file_size_bytes     BIGINT NOT NULL,
    mime_type           VARCHAR(100) NOT NULL DEFAULT 'application/pdf',
    page_count          SMALLINT,
    doc_type            finance.doc_type NOT NULL DEFAULT 'unknown',
    doc_type_confidence NUMERIC(4,3),
    status              finance.processing_status NOT NULL DEFAULT 'uploaded',
    error_detail        TEXT,
    extraction_model    VARCHAR(100),
    extraction_version  VARCHAR(20),
    raw_extraction      JSONB,                            -- Full LLM response (audit trail)
    extraction_tokens   JSONB,                            -- {input: N, output: M}
    overall_confidence  NUMERIC(4,3),
    content_embedding   vector(768),                      -- Document-level embedding
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at          TIMESTAMPTZ
);

-- IVFFlat vector similarity search index
CREATE INDEX idx_document_embedding ON finance.document
    USING ivfflat (content_embedding vector_cosine_ops) WITH (lists = 100);
```

### 4.2 `finance.party` — Vendors, Banks, Merchants

```sql
CREATE TABLE finance.party (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    canonical_name      VARCHAR(200) NOT NULL,
    display_name        VARCHAR(200) NOT NULL,
    role                finance.party_role NOT NULL,
    name_variants       TEXT[] NOT NULL DEFAULT '{}',      -- Array: {"COSTCO #1234", "COSTCO WHSE"}
    name_embedding      vector(768),                       -- Embedding for fuzzy matching
    CONSTRAINT uq_party_canonical UNIQUE (canonical_name, role)
);

-- Trigram GIN index enables fuzzy matching: "AMZN MKTP" → "Amazon"
CREATE INDEX idx_party_name_trgm ON finance.party USING GIN (canonical_name gin_trgm_ops);
CREATE INDEX idx_party_variants ON finance.party USING GIN (name_variants);
```

### 4.3 `finance.transaction` — Individual Transactions

```sql
CREATE TABLE finance.transaction (
    id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id            UUID NOT NULL REFERENCES finance.document(id) ON DELETE CASCADE,
    account_id             UUID NOT NULL REFERENCES finance.account(id),
    statement_cycle_id     UUID REFERENCES finance.statement_cycle(id),
    merchant_party_id      UUID REFERENCES finance.party(id),
    category_id            UUID REFERENCES finance.category(id),
    transaction_date       DATE NOT NULL,
    posting_date           DATE,
    description            VARCHAR(500) NOT NULL,
    normalized_description VARCHAR(500),
    amount                 NUMERIC(12,2) NOT NULL,          -- Never FLOAT for financial data
    currency               VARCHAR(3) NOT NULL DEFAULT 'USD',
    transaction_type       finance.transaction_type NOT NULL,
    confidence             NUMERIC(4,3),
    description_embedding  vector(768),
    created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at             TIMESTAMPTZ
);

-- Composite indexes for analytics queries
CREATE INDEX idx_txn_date_category ON finance.transaction (transaction_date, category_id);
CREATE INDEX idx_txn_account_date ON finance.transaction (account_id, transaction_date DESC);
-- Partial index: only non-deleted transactions
CREATE INDEX idx_txn_active ON finance.transaction (transaction_date DESC) WHERE deleted_at IS NULL;
-- Vector index for semantic search on descriptions
CREATE INDEX idx_txn_desc_embedding ON finance.transaction
    USING ivfflat (description_embedding vector_cosine_ops) WITH (lists = 100);
```

### 4.4 `finance.correction` — Human-in-the-Loop Corrections

```sql
CREATE TABLE finance.correction (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_type       VARCHAR(50) NOT NULL,
    entity_id         UUID NOT NULL,
    field_name        VARCHAR(100) NOT NULL,
    old_value         JSONB,
    new_value         JSONB NOT NULL,
    reason            TEXT,
    correction_source VARCHAR(50) NOT NULL DEFAULT 'manual',
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

### 4.5 `finance.processing_log` — Pipeline Audit Trail

```sql
CREATE TABLE finance.processing_log (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id    UUID NOT NULL REFERENCES finance.document(id) ON DELETE CASCADE,
    step_name      VARCHAR(50) NOT NULL,
    step_status    VARCHAR(20) NOT NULL,
    started_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at   TIMESTAMPTZ,
    duration_ms    INTEGER,
    model_used     VARCHAR(100),
    tokens_input   INTEGER,
    tokens_output  INTEGER,
    cost_usd       NUMERIC(8,6),
    error_detail   TEXT
);
```

---

## 5. Entity Relationship Diagram

```
                          ┌─────────────┐
                          │  category   │
                          │ (hierarchy) │
                ┌────────►│             │◄──────────────┐
                │         └─────────────┘               │
                │                                       │
┌───────────┐   │   ┌───────────────┐   ┌────────────┐ │   ┌──────────┐
│  document  │──┼──►│  transaction  │──►│ line_item  │─┘   │  party   │
│            │  │   │               │   │            │     │          │
│ file_hash  │  │   │ amount        │   │ quantity   │     │ variants │
│ status     │  │   │ description   │   │ unit_price │     │ role     │
│ doc_type   │  │   │ txn_date      │   │ total      │     └────┬─────┘
│ raw_json   │  │   └───────┬───────┘   └────────────┘          │
└─────┬──────┘  │           │                               issuer_id
      │         │           │ account_id                        │
      │    ┌────┴────────────────────┐         ┌───────────────┴──┐
      │    │   statement_cycle       │         │     account      │
      │    │   period_start/end      │◄────────│   masked_number  │
      │    │   balances              │         │   account_type   │
      │    │   reconciliation        │         │   credit_limit   │
      │    └─────────────────────────┘         └──────────────────┘
      │
      │    ┌─────────────────────────┐
      └───►│   processing_log       │
           │   step_name / status    │
           │   duration / cost       │
           └─────────────────────────┘
```

---

## 6. Data Type Decisions

| Decision | Chosen Type | Rationale |
|----------|-------------|-----------|
| Money amounts | `NUMERIC(12,2)` | Precise; avoids floating-point rounding errors |
| Primary keys | `UUID` | Distributed-safe; no sequence coordination |
| Timestamps | `TIMESTAMPTZ` | Always store with timezone; avoids conversion bugs |
| Flexible fields | `JSONB` | Indexable, containment queries |
| Merchant name variants | `TEXT[]` | Simple for small lists; GIN indexable |
| Embeddings | `vector(768)` | pgvector: single DB; sufficient at < 1M scale |
| Enums | Postgres `CREATE TYPE` | Type-safe, compact storage, enforced at DB level |

---

## 7. Database Security

| Concern | Implementation |
|---------|---------------|
| **Connection credentials** | Stored in GCP Secret Manager; injected as env vars |
| **Application DB user** | `fin_app_user` — SELECT, INSERT, UPDATE on `finance.*` (no DROP, TRUNCATE) |
| **NLQ read-only user** | `fin_nlq_readonly` — SELECT only (NLQ queries execute as this user) |
| **Migration user** | `fin_migration_user` — ALL on `finance.*` (used only by Alembic in CI/CD) |
| **SSL/TLS** | Enforce `sslmode=verify-full` for Cloud SQL connections |
| **PII handling** | Account numbers hashed (SHA-256); masked version stored separately |
| **Row-level security** | PostgreSQL `SET LOCAL` for future multi-tenant isolation |
| **Backup** | Cloud SQL automated daily backups; 7-day retention |
