# Architecture & Patterns — Modular Clean Architecture

> **Financial Insights Hub** · Python 3.12 · FastAPI · SQLAlchemy 2.0

---

## 1. Architecture Philosophy

| Principle | What It Means | Why It Matters |
|-----------|--------------|----------------|
| **Separation of Concerns** | Each module owns one responsibility | Testable, replaceable, understandable |
| **Dependency Inversion** | Core logic depends on abstractions, not implementations | Swap DB, LLM provider, or storage without touching business logic |
| **Hexagonal (Ports & Adapters)** | Business logic at center; external systems are adapters | LLM, database, storage, queue are all pluggable |
| **Explicit Boundaries** | Modules communicate via defined interfaces and DTOs | No hidden coupling; clear API contracts |
| **Convention over Configuration** | Consistent naming, folder structure, patterns | Any engineer can navigate the codebase instantly |

---

## 2. Project Structure

```
backend/
├── pyproject.toml                    # Project metadata + dependencies
├── alembic.ini                       # Migration config
├── Dockerfile                        # Backend container
├── Dockerfile.worker                 # Worker container
├── alembic/                          # Database migrations
│   ├── env.py
│   └── versions/
│       └── 001_initial_schema.py
├── src/
│   └── fin_insights/                 # Main Python package
│       ├── __init__.py
│       ├── main.py                   # FastAPI app entry point
│       ├── config.py                 # Pydantic-based settings
│       ├── dependencies.py           # DI wiring
│       │
│       ├── api/                      # Presentation Layer
│       │   ├── routes/               # FastAPI routers
│       │   │   ├── documents.py
│       │   │   ├── transactions.py
│       │   │   ├── analytics.py
│       │   │   ├── nlq.py
│       │   │   └── health.py
│       │   ├── schemas/              # Request/Response DTOs
│       │   │   ├── document_schemas.py
│       │   │   ├── transaction_schemas.py
│       │   │   └── analytics_schemas.py
│       │   └── middleware/           # Cross-cutting concerns
│       │       ├── error_handler.py
│       │       ├── request_logging.py
│       │       └── correlation_id.py
│       │
│       ├── domain/                   # Domain Layer (Entities + Value Objects)
│       │   ├── models/               # Pydantic domain models
│       │   │   ├── document.py
│       │   │   ├── party.py
│       │   │   ├── account.py
│       │   │   ├── transaction.py
│       │   │   ├── line_item.py
│       │   │   └── enums.py
│       │   ├── interfaces/           # Abstract base classes (Protocols)
│       │   │   ├── document_repository.py
│       │   │   ├── storage_service.py
│       │   │   ├── extraction_service.py
│       │   │   └── classification_service.py
│       │   └── events/
│       │       └── document_events.py
│       │
│       ├── services/                 # Application Layer (Use Cases)
│       │   ├── ingestion_service.py
│       │   ├── classification_service.py
│       │   ├── extraction_service.py
│       │   ├── validation_service.py
│       │   ├── normalization_service.py
│       │   ├── analytics_service.py
│       │   └── nlq_service.py
│       │
│       ├── adapters/                 # Infrastructure Layer (Implementations)
│       │   ├── db/
│       │   │   ├── session.py        # SQLAlchemy async engine/session
│       │   │   ├── models.py         # SQLAlchemy ORM models
│       │   │   └── repositories/
│       │   │       ├── document_repo.py
│       │   │       ├── transaction_repo.py
│       │   │       └── party_repo.py
│       │   ├── storage/
│       │   │   └── gcs_storage.py
│       │   ├── llm/
│       │   │   ├── vertex_client.py  # Gemini API wrapper
│       │   │   ├── prompts/
│       │   │   │   ├── classification.py
│       │   │   │   ├── extraction.py
│       │   │   │   └── nlq.py
│       │   │   └── extraction_adapter.py
│       │   ├── ocr/
│       │   │   └── document_ai_ocr.py
│       │   └── queue/
│       │       └── celery_tasks.py
│       │
│       ├── pipeline/                 # Document Processing Pipeline
│       │   ├── orchestrator.py
│       │   ├── steps.py
│       │   └── retry_policy.py
│       │
│       └── shared/                   # Cross-cutting utilities
│           ├── logging.py
│           ├── exceptions.py
│           ├── constants.py
│           └── utils.py
│
└── tests/
    ├── conftest.py
    ├── unit/
    ├── integration/
    ├── e2e/
    └── golden/                       # Golden dataset test data
```

---

## 3. Layer Interaction Rules

### Dependency Flow (Strict — Enforced by import-linter)
```
api/ ──────→ services/ ──────→ domain/
                │                  ↑
                │                  │ (depends on interfaces)
                ↓                  │
             adapters/ ────────────┘
             (implements interfaces from domain/)
```

### Rules
| Rule | Description |
|------|-------------|
| **R1** | `domain/` depends on NOTHING (no imports from other packages) |
| **R2** | `services/` depends on `domain/` only (uses interfaces) |
| **R3** | `adapters/` implements interfaces from `domain/`, depends on `domain/` |
| **R4** | `api/` depends on `services/` and `domain/` (for DTOs) |
| **R5** | `pipeline/` depends on `services/` to orchestrate steps |
| **R6** | `shared/` can be used by any layer (logging, exceptions) |

### Enforcement
Dependency rules are enforced at build time using `import-linter`:
```toml
[tool.importlinter]
root_packages = ["fin_insights"]

[[tool.importlinter.contracts]]
name = "Domain layer independence"
type = "forbidden"
source_modules = ["fin_insights.domain"]
forbidden_modules = ["fin_insights.services", "fin_insights.adapters", "fin_insights.api"]
```

---

## 4. Key Design Patterns

### 4.1 Repository Pattern (Protocol-Based)
```python
# domain/interfaces/document_repository.py
from typing import Protocol

class DocumentRepository(Protocol):
    async def get_by_id(self, doc_id: uuid.UUID) -> Document | None: ...
    async def save(self, document: Document) -> Document: ...
    async def list_all(self, limit: int = 50, offset: int = 0) -> list[Document]: ...
```

### 4.2 Strategy Pattern (LLM extraction per document type)
```python
class ExtractionService:
    def __init__(self, strategies: dict[DocType, ExtractionStrategy]):
        self._strategies = strategies

    async def extract(self, doc_type: DocType, text: str) -> ExtractionResult:
        strategy = self._strategies[doc_type]
        return await strategy.extract(text)
```

### 4.3 Pipeline Pattern (document processing)
```python
class DocumentPipeline:
    steps: list[PipelineStep] = [
        ClassificationStep(),
        OCRStep(),
        ExtractionStep(),
        ValidationStep(),
        NormalizationStep(),
        StorageStep(),
    ]

    async def process(self, document_id: uuid.UUID) -> PipelineResult:
        context = PipelineContext(document_id=document_id)
        for step in self.steps:
            context = await step.execute(context)
            if context.failed:
                break
        return context.result
```

### 4.4 Result Pattern (explicit error handling)
```python
@dataclass
class Result[T]:
    value: T | None = None
    error: str | None = None
    is_success: bool = True

    @classmethod
    def ok(cls, value: T) -> "Result[T]":
        return cls(value=value, is_success=True)

    @classmethod
    def fail(cls, error: str) -> "Result[T]":
        return cls(error=error, is_success=False)
```

---

## 5. Configuration Management

### Settings Pattern (Pydantic-based)
```python
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # Database
    db_host: str = "localhost"
    db_port: int = 5432
    db_name: str = "financial_insights"

    # GCP
    gcp_project_id: str = ""
    vertex_ai_location: str = "us-central1"

    # LLM
    extraction_model: str = "gemini-2.5-flash"
    classification_model: str = "gemini-2.5-flash"
    nlq_model: str = "gemini-2.5-pro"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # App
    environment: str = "development"
    log_level: str = "INFO"
    cors_origins: list[str] = ["http://localhost:5173"]

    model_config = {"env_prefix": "FIN_", "env_file": ".env"}
```

### Environment Strategy
| Environment | Config Source | Database | LLM | Storage |
|-------------|-------------|----------|-----|---------|
| **Local Dev** | `.env` file | Docker Postgres | Vertex AI (real) or mock | Local filesystem |
| **CI/Test** | GitHub Secrets | Docker Postgres (testcontainers) | Mock/recorded | Local filesystem |
| **Production** | Secret Manager + env vars | Cloud SQL | Vertex AI | Cloud Storage |

---

## 6. Error Handling Strategy

### Exception Hierarchy
```python
class FinInsightsError(Exception):
    """Base exception for all application errors."""

class DocumentNotFoundError(FinInsightsError): ...
class DuplicateDocumentError(FinInsightsError): ...
class ExtractionError(FinInsightsError): ...
class ValidationError(FinInsightsError): ...
class ReconciliationError(ValidationError): ...
class StorageError(FinInsightsError): ...
class LLMError(FinInsightsError): ...
class RateLimitError(LLMError): ...
```

### Global Error Handler
```python
@app.exception_handler(FinInsightsError)
async def handle_app_error(request: Request, exc: FinInsightsError):
    status_map = {
        DocumentNotFoundError: 404,
        DuplicateDocumentError: 409,
        ValidationError: 422,
        RateLimitError: 429,
    }
    status = status_map.get(type(exc), 500)
    return JSONResponse(status_code=status, content={"error": str(exc)})
```

---

## 7. API Design Standards

### RESTful Conventions
| Endpoint | Method | Description | Response |
|----------|--------|-------------|----------|
| `/api/v1/documents` | POST | Upload document | 201 + document metadata |
| `/api/v1/documents` | GET | List documents (paginated) | 200 + list |
| `/api/v1/documents/{id}` | GET | Get document detail + extraction | 200 + detail |
| `/api/v1/documents/{id}/reprocess` | POST | Re-run extraction pipeline | 202 + job status |
| `/api/v1/transactions` | GET | List transactions (filtered) | 200 + paginated list |
| `/api/v1/analytics/spending` | GET | Spending aggregations | 200 + analytics data |
| `/api/v1/analytics/trends` | GET | Monthly/weekly trends | 200 + time series |
| `/api/v1/nlq` | POST | Natural language query | 200 + SQL + results |
| `/api/v1/chat` | POST | Conversational AI (SSE stream) | 200 + SSE stream |
| `/api/v1/health` | GET | Health check | 200 + status |

### Standard Response Envelope
```json
{
  "data": { ... },
  "meta": {
    "page": 1,
    "page_size": 20,
    "total_count": 142,
    "total_pages": 8
  },
  "errors": []
}
```

---

## 8. Dependency Management

### Core Dependencies

| Package | Purpose |
|---------|---------|
| `fastapi` | Async web framework (ASGI) |
| `uvicorn` | ASGI server |
| `sqlalchemy[asyncio]` | Async ORM |
| `alembic` | Database migrations |
| `pydantic` | Data validation + serialization |
| `pydantic-settings` | Configuration management |
| `httpx` | Async HTTP client |
| `celery[redis]` | Distributed task queue |
| `google-cloud-aiplatform` | Vertex AI SDK |
| `google-cloud-storage` | GCS SDK |
| `pgvector` | Vector similarity search |
| `structlog` | Structured logging |
| `tenacity` | Retry + resilience |
| `opentelemetry-sdk` | Distributed tracing |

### Dev Dependencies

| Package | Purpose |
|---------|---------|
| `pytest` / `pytest-asyncio` | Test framework |
| `pytest-cov` | Coverage reporting |
| `testcontainers` | Containerized test DB |
| `ruff` | Linter + Formatter |
| `mypy` | Type checking |
| `import-linter` | Dependency rule enforcement |
| `pre-commit` | Git hooks (lint, format, security checks) |
