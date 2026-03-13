# Multi-Tenant Security Architecture

> **Financial Insights Hub** · Firebase Auth · PostgreSQL RLS · AES-256 Encryption

---

## 1. Authentication Architecture

### Firebase Auth Integration
```
┌──────────┐     ┌──────────────┐     ┌─────────────┐     ┌──────────────┐
│  React   │────►│  Firebase    │────►│  FastAPI     │────►│  PostgreSQL  │
│  Client  │     │  Auth SDK    │     │  Middleware   │     │  (SET LOCAL) │
│          │     │              │     │              │     │              │
│  Login   │     │  JWT Token   │     │  Verify JWT  │     │  RLS with    │
│  Sign-up │     │  Refresh     │     │  Extract UID │     │  tenant_id   │
│  OAuth   │     │  Management  │     │  Set context │     │  isolation   │
└──────────┘     └──────────────┘     └─────────────┘     └──────────────┘
```

### Supported Auth Methods
| Method | Provider | Use Case |
|--------|----------|----------|
| Email/Password | Firebase | Primary authentication |
| Google OAuth | Firebase (Google provider) | Social login |
| Magic Link | Firebase (email link) | Passwordless option |

---

## 2. Row-Level Security (RLS)

### PostgreSQL RLS Policy
```sql
-- Enable RLS on all tenant-scoped tables
ALTER TABLE finance.document ENABLE ROW LEVEL SECURITY;
ALTER TABLE finance.transaction ENABLE ROW LEVEL SECURITY;

-- Policy: Users can only see their own data
CREATE POLICY tenant_isolation ON finance.document
    USING (tenant_id = current_setting('app.current_tenant')::uuid);

CREATE POLICY tenant_isolation ON finance.transaction
    USING (tenant_id = current_setting('app.current_tenant')::uuid);
```

### FastAPI Middleware — Tenant Context
```python
@app.middleware("http")
async def set_tenant_context(request: Request, call_next):
    """Set PostgreSQL session variable for RLS enforcement."""
    tenant_id = request.state.user.tenant_id
    async with get_session() as session:
        await session.execute(
            text("SET LOCAL app.current_tenant = :tid"),
            {"tid": str(tenant_id)}
        )
        request.state.session = session
        response = await call_next(request)
    return response
```

**Key design**: `SET LOCAL` scopes the setting to the current transaction. If the middleware fails to set the tenant, RLS blocks all data access (fail-closed).

---

## 3. Document Encryption

### Envelope Encryption (AES-256-GCM)
```
┌──────────────┐     ┌──────────────────┐     ┌───────────────────┐
│  Document    │     │  Data Encryption  │     │  Key Encryption   │
│  (PDF bytes) │────►│  Key (DEK)       │────►│  Key (KEK)        │
│              │     │                  │     │                   │
│  Encrypted   │     │  Random per doc  │     │  GCP KMS         │
│  with DEK    │     │  AES-256-GCM     │     │  Managed by cloud │
└──────────────┘     └──────────────────┘     └───────────────────┘
```

- Each document encrypted with a unique DEK (Data Encryption Key)
- DEK is encrypted by a KEK (Key Encryption Key) managed in GCP KMS
- Encrypted DEK stored alongside the document in GCS metadata
- PDFs are encrypted at rest and in transit

---

## 4. API Security Hardening

| Control | Implementation |
|---------|---------------|
| **Rate Limiting** | Per-user: 100 req/min; Per-IP: 200 req/min; Upload: 10/min |
| **CORS** | Strict origin allowlist (production domain only) |
| **CSP** | Content-Security-Policy headers on all responses |
| **Input Validation** | Pydantic v2 schemas on all API inputs |
| **File Upload** | MIME validation, magic bytes check, 20 MB limit |
| **SQL Injection** | NLQ queries run as read-only DB user with timeout |
| **PII Redaction** | Account numbers masked before logging |

---

## 5. Secrets Management

| Secret | Storage | Rotation |
|--------|---------|----------|
| Database credentials | GCP Secret Manager | 90-day rotation |
| Firebase admin SDK key | GCP Secret Manager | On-demand |
| Vertex AI service account | Workload Identity | Auto-managed |
| Redis credentials | GCP Secret Manager | 90-day rotation |
| API keys | GCP Secret Manager | On revocation |

Local development uses `.env` files (excluded from git). Production injects secrets via environment variables from Secret Manager.

---

## 6. Audit Logging

All security-relevant events are logged:

| Event | Data Logged |
|-------|-------------|
| Login / Logout | User ID, timestamp, IP, auth method |
| Document upload | User ID, file hash, document ID, source |
| Document access | User ID, document ID, resource accessed |
| NLQ query | User ID, SQL generated, execution result |
| Correction | User ID, entity, field, old/new value |
| Admin action | Actor, target, action, timestamp |

---

## 7. Subscription Tiers

| Feature | Free | Pro | Business |
|---------|------|-----|----------|
| Documents/month | 10 | 100 | Unlimited |
| NLQ queries/day | 5 | 50 | Unlimited |
| Chat messages/day | 10 | 100 | Unlimited |
| RAG answers/day | 5 | 50 | Unlimited |
| Data retention | 1 year | 3 years | Unlimited |
| Export formats | CSV | CSV, Excel | CSV, Excel, API |
| Support | Community | Email | Priority |
