# Adaptive Document Ingestion — Multi-Channel Architecture

> **Financial Insights Hub** · 6 Ingestion Channels · Unified Pipeline

---

## Overview

The platform supports multiple document ingestion channels that feed into a unified processing pipeline. Each channel handles acquisition differently but converges to the same format: a file (or structured data) entering the extraction pipeline.

---

## Ingestion Channels

### Channel 1: Manual Upload (Implemented)
Direct file upload via the web UI or API. Supports PDF, PNG, JPG, CSV.

### Channel 2: Email Forwarding
- Users forward financial statements to a dedicated email address
- SendGrid inbound parse webhook extracts attachments
- Auto-creates documents from PDF/image attachments
- Filters non-financial attachments via classification

### Channel 3: Financial API Integration (Plaid)
- Plaid Link for secure bank account connection
- Pull transactions directly via API (no document needed)
- Deduplication: Match Plaid transactions against already-extracted document transactions using amount + date + description fuzzy matching
- Reconciliation: Plaid becomes the "source of truth" for transaction amounts

### Channel 4: Cloud Folder Sync
- Connect Google Drive, OneDrive, or Dropbox
- Watch a designated folder for new files
- Auto-ingest when new PDFs appear
- Polling-based (15-minute intervals) or webhook-based where supported

### Channel 5: Mobile PWA Camera Capture
- Progressive Web App with camera access
- Capture receipt photos directly
- On-device image optimization (resize, compress, enhance contrast)
- Upload compressed image for OCR + extraction

### Channel 6: Browser Extension
- Chrome/Firefox extension for web-based statements
- "Save to Financial Insights" button on banking websites
- Captures rendered HTML → converts to structured data
- Bypasses PDF download step entirely

---

## Unified Ingestion Architecture

```
Channel 1: Upload API ─────┐
Channel 2: Email Webhook ──┤
Channel 3: Plaid API ──────┼────► Ingestion Gateway ────► Processing Pipeline
Channel 4: Cloud Sync ─────┤         │                     (classify → extract →
Channel 5: Mobile PWA ─────┤         │                      validate → store)
Channel 6: Browser Ext ────┘         │
                                     ▼
                              Deduplication
                              (SHA-256 hash + fuzzy matching)
```

### Ingestion Gateway Responsibilities
1. **Normalize**: Convert source-specific format to unified `IngestRequest`
2. **Deduplicate**: SHA-256 file hash check + fuzzy matching for near-duplicates
3. **Validate**: File type, size limits, security scan
4. **Enqueue**: Submit to processing pipeline (Celery task)
5. **Track**: Create `document` record with source metadata

---

## Implementation Roadmap

| Priority | Channel | Complexity | Status |
|----------|---------|-----------|--------|
| P0 | Manual Upload (API + UI) | Low | **Implemented** |
| P1 | Email Forwarding | Medium | Design complete |
| P1 | Plaid Integration | Medium | Design complete |
| P2 | Cloud Folder Sync | Medium | Spec'd |
| P2 | Mobile PWA Camera | Low-Medium | Spec'd |
| P3 | Browser Extension | High | Future |
