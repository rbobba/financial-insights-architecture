# ADR: Extraction Model Decision — Gemini 2.5 Flash & Smart Page Filtering

> **Status**: Approved | **Date**: 2026-02-24

---

## Decision Summary

| Aspect | Decision |
|--------|----------|
| **Extraction model** | `gemini-2.5-flash` (upgraded from `gemini-2.0-flash`) |
| **Classification model** | `gemini-2.0-flash` (unchanged) |
| **NLQ model** | `gemini-2.5-pro` (unchanged) |
| **Embedding model** | `text-embedding-004` (unchanged) |
| **Future optimization** | Smart page filtering (Phase 2) |

---

## Problem Statement

After uploading ~54 new financial documents (brokerage statements and tax forms), **5 documents consistently failed extraction**. Root-cause analysis revealed three compounding bugs:

1. **Missing `max_output_tokens`** — `_call_gemini()` created a `GenerationConfig` without specifying `max_output_tokens`, resulting in truncated JSON on large outputs.

2. **Wrong logger variable** — `_get_or_create_party()` referenced `log` instead of `logger` inside an `except IntegrityError` block. When the race-condition path triggered, it raised `NameError`, masking the real error.

3. **Gemini 2.0 Flash hard output limit** — 2.0 Flash has a **hard maximum of 8,192 output tokens**. Large brokerage statements (18–30 pages, 40–80 transactions) require 10,000–16,000 output tokens. No configuration can overcome this limit.

---

## Why Gemini 2.5 Flash

| Capability | Gemini 2.0 Flash | Gemini 2.5 Flash |
|------------|-------------------|-------------------|
| Max output tokens | **8,192** | **65,536** |
| Context window | 1M tokens | 1M tokens |
| Native PDF input | Yes | Yes |
| Structured output | Yes | Yes |
| JSON quality | Truncation on large docs | Reliable (w/ repair fallback) |
| Thinking/reasoning | No | Yes |

**Key justification**: 65K output token limit accommodates largest brokerage statements (~16K tokens) with significant headroom.

### Alternatives Considered

| Alternative | Why Not Chosen |
|-------------|----------------|
| Keep 2.0 Flash + chunk extraction | High engineering cost, fragile for multi-page tables |
| Keep 2.0 Flash + prompt trimming | Negligible savings vs. the output token bottleneck |
| Switch to Gemini 2.5 Pro | 4x more expensive; unnecessary for structured extraction |

---

## Cost Analysis

### Per-Document Cost

| Model | Input Cost | Output Cost | **Total/Doc** |
|-------|-----------|-------------|---------------|
| Gemini 2.0 Flash | $0.0008 | $0.0020 | **$0.0028** |
| Gemini 2.5 Flash | $0.0012 | $0.0063 | **$0.0075** |
| Gemini 2.5 Pro | $0.0100 | $0.0500 | **$0.0600** |

**Verdict**: The ~$0.005/doc increase is negligible. At 10,000 documents, the total difference is under $50.

### Cross-Vendor Comparison

| Model | Total/Doc | Native PDF | Output Limit |
|-------|-----------|-----------|-------------|
| **Gemini 2.5 Flash** | **$0.0075** | **Yes** | **65K** |
| GPT-5 mini | $0.0120 | No | 16K |
| GPT-4.1 mini | $0.0112 | No | 32K |
| Claude Haiku 4.5 | $0.0330 | No | 64K |
| Mistral Small | $0.0023 | No | 32K |

Gemini 2.5 Flash wins on:
1. **Native PDF support** — accepts raw PDF bytes; no conversion needed
2. **Cheapest viable option** — only non-PDF models are cheaper
3. **65K output tokens** — highest among flash-tier models
4. **Already integrated** — single Vertex AI SDK for all AI capabilities

---

## JSON Repair Strategy

Even with 2.5 Flash, LLM output occasionally has malformed JSON. A `_repair_json()` fallback handles:

| Failure Mode | Repair Strategy |
|--------------|-----------------|
| Unterminated string in last transaction | Remove incomplete fragment, close structure |
| Missing `]` / `}` at end | Add missing closers |
| Trailing comma after last element | Strip trailing comma |
| Corruption mid-document | Progressive truncation search |

---

## Smart Page Filtering (Phase 2 — Future)

Financial documents contain transactional pages (extractable data) and informational pages (disclosures, legal notices). Filtering informational pages before extraction can:

- Reduce input tokens by 40–60%
- Reduce noise in extraction output
- Cut cost further

**Approach**: Hybrid keyword heuristic + cheap LLM classification for ambiguous pages.

---

## Validation

After applying all fixes, **all 132 documents** (including the 5 previously failed) were successfully processed:

| Document | Pages | Transactions | Processing Time |
|----------|-------|-------------|-----------------|
| Brokerage Statement 2025-10-31 | 30 | 71 | 76.9s |
| Brokerage Statement 2025-09-30 | 22 | 46 | 76.2s |
| 1099 Composite 2024 | 18 | 32 | 86.1s |
| Brokerage Statement 2024-01-31 | 12 | 14 | 64.9s |
| Brokerage Statement 2025-08-31 | 18 | 2 | 74.7s |

**Total**: 165 new transactions extracted from the 5 previously-failed documents.
