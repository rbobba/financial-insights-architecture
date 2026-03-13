# Infrastructure, Cross-Cutting Concerns, Performance & Security

> **Financial Insights Hub** · Docker · GCP · OpenTelemetry · Redis

---

## 1. Docker & Container Strategy

### Container Images

| Image | Base | Purpose | Target Size |
|-------|------|---------|-------------|
| `fin-insights-api` | `python:3.12-slim` | FastAPI application server | < 200 MB |
| `fin-insights-worker` | `python:3.12-slim` | Celery worker for async document processing | < 250 MB |
| `fin-insights-web` | `node:20-alpine` → `nginx:alpine` (multi-stage) | React frontend (static build served by nginx) | < 50 MB |

### Docker Compose — 8-Service Local Development Stack

```yaml
services:
  postgres:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_DB: financial_insights
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres"]

  redis:
    image: redis:7-alpine
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]

  api:
    build: ./backend
    depends_on:
      postgres: { condition: service_healthy }
      redis: { condition: service_healthy }
    volumes:
      - ./backend/src:/app/src   # Hot reload in dev

  worker:
    build:
      context: ./backend
      dockerfile: Dockerfile.worker
    depends_on:
      postgres: { condition: service_healthy }
      redis: { condition: service_healthy }

  web:
    build: ./frontend
    depends_on: [api]

  otel-collector:
    image: otel/opentelemetry-collector-contrib:0.114.0
    
  jaeger:
    image: jaegertracing/jaeger:2.1.0

  prometheus:
    image: prom/prometheus:v3.1.0
```

---

## 2. CI/CD Pipeline (GitHub Actions)

### Quality Gates

| Gate | Threshold | Blocks Deploy? |
|------|-----------|---------------|
| Ruff lint (no errors) | 0 errors | Yes |
| Ruff format check | Fully formatted | Yes |
| mypy type check | 0 errors | Yes |
| Unit test pass rate | 100% | Yes |
| Backend code coverage | ≥ 80% | Yes |
| Frontend test pass rate | 100% | Yes |
| Extraction accuracy (golden dataset) | ≥ 95% | Yes |
| Reconciliation pass rate | ≥ 98% | Yes |
| Import dependency rules | All passing | Yes |

---

## 3. Observability & Monitoring

### 3.1 OpenTelemetry Distributed Tracing

The platform uses OpenTelemetry SDK 1.40 with GenAI semantic conventions for full observability:

- **Traces**: Every API request creates a trace that spans document processing, LLM calls, and DB queries
- **GenAI Spans**: LLM calls are instrumented with `gen_ai.*` attributes (model, temperature, token counts, finish reason)
- **Metrics**: Token usage histograms, LLM call counters, error counters, latency distributions
- **Logs**: Trace-correlated structured logging via structlog

**Stack**: OTel Collector (contrib 0.114.0) → Jaeger (traces) + Prometheus (metrics)

### 3.2 Structured Logging

```python
import structlog

def configure_logging(environment: str, log_level: str = "INFO"):
    processors = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
    ]
    if environment == "production":
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer())
    structlog.configure(processors=processors)
```

### 3.3 Log Standards

| Field | Description | Example |
|-------|-------------|---------|
| `correlation_id` | Request trace ID (propagated through pipeline) | `req_abc123` |
| `document_id` | Document being processed | `uuid` |
| `step` | Pipeline step name | `classification`, `extraction` |
| `duration_ms` | Operation duration | `1234` |
| `model` | LLM model used | `gemini-2.5-flash` |
| `token_count` | Tokens consumed | `{input: 500, output: 200}` |

### 3.4 Monitoring & Alerting

| Metric | Source | Alert Threshold |
|--------|--------|----------------|
| API error rate (5xx) | Cloud Run metrics | > 5% over 5 min |
| API latency p95 | Cloud Run metrics | > 2s |
| Document processing failures | Custom metric | > 3 consecutive failures |
| LLM API errors | Custom metric | > 10% error rate |
| DB connection pool exhaustion | SQLAlchemy metrics | > 80% utilization |
| Extraction accuracy drift | Custom eval metric | Drops below 90% |

### 3.5 Health Check Endpoints

```python
@router.get("/health/ready")
async def readiness(db: AsyncSession = Depends(get_session)):
    checks = {
        "database": await check_db(db),
        "redis": await check_redis(),
        "storage": await check_gcs(),
        "vertex_ai": await check_vertex(),
    }
    all_healthy = all(c["status"] == "ok" for c in checks.values())
    return JSONResponse(
        status_code=200 if all_healthy else 503,
        content={"status": "ready" if all_healthy else "degraded", "checks": checks}
    )
```

---

## 4. Security

### 4.1 Security Matrix

| Category | Requirement | Implementation |
|----------|-------------|----------------|
| **Secrets** | No hardcoded credentials | `pydantic-settings` + Secret Manager + `.env` (local) |
| **API Auth** | Protect API endpoints | API key header (v1); OAuth2/JWT (v2) |
| **HTTPS** | All traffic encrypted in transit | Cloud Run enforces HTTPS |
| **Encryption at Rest** | PDFs and DB encrypted | GCS + Cloud SQL default encryption (AES-256) |
| **CORS** | Restrict cross-origin requests | FastAPI CORSMiddleware — allowed origins list |
| **Input Validation** | Validate all user input | Pydantic schemas on all API inputs |
| **File Upload** | Validate file type & size | MIME type check, max 20 MB, malware scan (optional) |
| **SQL Injection** | Prevent injection from NLQ | Read-only queries; parameterized; restricted DB user |
| **PII in Logs** | No sensitive data in logs | Redact account numbers, SSN patterns |
| **Dependency Scanning** | Vulnerable package detection | `pip-audit` in CI; GitHub Dependabot |
| **Container Security** | Non-root; minimal base image | `USER appuser` in Dockerfile; `python:3.12-slim` |

### 4.2 NLQ-to-SQL Security (Critical Path)

The NLQ feature generates SQL from natural language — this is the **highest security risk**:

```
Mitigation Stack:
1. LLM generates SQL → parsed & validated
2. SQL analyzed for prohibited operations (DROP, DELETE, UPDATE, INSERT, ALTER)
3. Only SELECT statements allowed
4. Query runs against read-only DB user with restricted permissions
5. Query timeout enforced (max 10 seconds)
6. Result set size limited (max 1000 rows)
7. All generated SQL is logged for audit
```

---

## 5. Performance

### 5.1 Database Performance

| Strategy | Implementation | Impact |
|----------|---------------|--------|
| **Connection Pooling** | SQLAlchemy async pool (pool_size=5, max_overflow=10) | Prevents connection exhaustion |
| **Indexing** | B-tree on FKs, GIN on JSONB, IVFFlat on vectors, trigram for fuzzy search | Fast filtered queries |
| **Pagination** | Keyset pagination (cursor-based) for large result sets | Consistent performance |
| **Query Optimization** | `EXPLAIN ANALYZE` on all analytics queries; avoid N+1 | Critical for dashboard |

### 5.2 LLM Performance

| Strategy | Implementation | Impact |
|----------|---------------|--------|
| **Model Selection** | Flash for classification + extraction, Pro for complex NLQ | Cost vs. accuracy balance |
| **Caching** | Hash document content → cache extraction results | Skip re-processing identical docs |
| **Streaming** | SSE for chat/RAG responses (token-by-token) | Better perceived latency |
| **Timeout** | 30-second timeout per LLM call; retry with exponential backoff | Prevent hung requests |
| **Fallback** | If Pro fails, retry with Flash; if all fail, mark for manual review | Graceful degradation |

### 5.3 Frontend Performance

| Strategy | Implementation |
|----------|---------------|
| **Code Splitting** | React lazy() + Suspense per route |
| **Virtualized Lists** | TanStack Virtual for transaction lists (1000+ rows) |
| **Optimistic Updates** | Update UI before server confirms |
| **SSE Reconnection** | Auto-reconnect with exponential backoff on stream disconnect |

---

## 6. Retry & Resilience

### Retry Policy (Tenacity)
```python
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    retry=retry_if_exception_type((LLMError, ConnectionError)),
    before_sleep=log_retry_attempt,
)
async def call_vertex_ai(prompt: str, model: str) -> str:
    ...
```

### Circuit Breaker

| Service | Failure Threshold | Recovery Time | Fallback |
|---------|------------------|---------------|----------|
| Vertex AI | 5 failures in 60s | 120s cooldown | Queue document for later; return 503 |
| Cloud SQL | 3 failures in 30s | 60s cooldown | Return cached data if available |
| Cloud Storage | 3 failures in 30s | 60s cooldown | Return 503 |

### Idempotency

| Operation | Idempotency Key | Behavior |
|-----------|----------------|----------|
| Document upload | SHA-256 hash of file content | Reject duplicate; return existing document |
| Document processing | `document_id` + `pipeline_version` | Skip if already processed with same version |
| NLQ query | Query text hash + TTL | Return cached result within TTL window |
