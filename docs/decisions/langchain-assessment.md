# ADR: LangChain Assessment — Build vs. Buy Decision

> **Status**: Approved | **Date**: 2026-02-24  
> **Verdict**: **Do NOT adopt LangChain.** Continue with hand-built architecture. Adopt targeted best-of-breed libraries instead.

---

## Executive Summary

This platform already has a well-architected LLM integration layer. LangChain would add abstraction bloat, debugging opacity, and dependency churn without meaningful capability gains. Instead, we recommend **four targeted libraries** that each solve a specific gap:

| Library | Solves | LangChain Equivalent |
|---------|--------|---------------------|
| **LangFuse** | Observability, cost tracking, prompt management | LangSmith (paid, vendor lock-in) |
| **Ragas** | RAG evaluation metrics | N/A (no equivalent) |
| **Instructor** | Structured LLM output with validation | `PydanticOutputParser` |
| **LiteLLM** | Multi-provider LLM abstraction | `ChatOpenAI`, `ChatVertexAI`, etc. |

---

## Component-by-Component Comparison

| LangChain Component | What It Does | Our Equivalent | Status |
|---------------------|-------------|----------------|:------:|
| **ChatModel** | Wraps Gemini/GPT API calls | `GeminiAdapter` in `adapters/llm/` | Built |
| **PromptTemplate** | Parameterizes prompts | Versioned prompts in `prompts/v1/*.py` | Built |
| **OutputParser** | Parses LLM JSON responses | `ExtractionResult` models + JSON parsing | Built |
| **Chain** | Sequential LLM call pipelines | `PipelineRunner` + `PipelineStep` Protocol | Built (better) |
| **AgentExecutor** | ReAct agent loop | `AgentOrchestrator` with tool dispatch | Built |
| **Tool** | Agent-callable functions | `AgentTools` with SQL, RAG, analytics | Built |
| **VectorStore** | pgvector integration | `EmbeddingService` + pgvector queries | Built |
| **Retriever** | RAG document retrieval | `RagService.search()` with cosine similarity | Built |
| **Memory** | Chat history management | `chat_session` + `chat_message` tables | Built |
| **Document Loader** | PDF/file parsing | `PdfParser` → text extraction pipeline | Built |
| **Text Splitter** | Chunk documents | `ChunkService` with configurable overlap | Built |
| **Callback Handler** | Logging, tracing | `processing_log` table + structured logging | Built |

**Score: 12/12 components already implemented.** LangChain provides zero new capabilities.

---

## Why NOT LangChain

### 1. Abstraction Tax
LangChain wraps simple API calls in 5+ layers of abstraction:
```
LangChain: Your code → Chain → LLMChain → BaseChatModel → ChatVertexAI → vertexai.generate_content()
Ours:      Your code → GeminiAdapter.generate() → vertexai.generate_content()
```
Each layer adds serialization overhead, error wrapping, callback dispatching. Stack traces are 40+ frames deep.

### 2. Dependency Bloat
```
LangChain ecosystem: 200+ transitive dependencies
Our current LLM stack: ~15 transitive dependencies
```
More dependencies = more security vulnerabilities, version conflicts, build time.

### 3. Version Instability
LangChain's rapid iteration cycle means API changes between minor versions, deprecation warnings in every release, and migration guides needed quarterly. For a financial platform where reliability matters, this churn is unacceptable.

### 4. Debugging Opacity
When an LLM call fails in our code: the raw `GoogleAPICallError`, the prompt is a Python string you can print, and the adapter has explicit try/except with domain-specific error types.

### 5. Where Our Architecture Is Better
- **Pipeline Runner**: Protocol-based steps with `StepOutcome` enum — explicit state machine vs. rigid chain abstraction
- **Prompt Versioning**: Separate Python modules per version — IDE support, type hints, git-diffable
- **Agent Orchestrator**: Explicit ReAct loop with step tracking + logging vs. opaque internal loop
- **Database Integration**: Native SQLAlchemy 2.0 async + pgvector with actual schema joins
- **Testing**: Each component is a plain class with DI — no framework magic to mock

---

## Recommended Targeted Libraries

### LangFuse — LLM Observability
Full trace visualization, cost tracking per feature/model, prompt version A/B testing, human evaluation UI. Self-hostable.

### Ragas — RAG Evaluation
Automated metrics for RAG quality: faithfulness, answer relevancy, context precision, context recall. Integrates with golden dataset eval.

### Instructor — Structured Output Validation
Pydantic-based validation with automatic retry on schema violations. Handles the "LLM returns almost-right JSON" problem.

### LiteLLM — Multi-Provider Abstraction
Unified API for Vertex AI, OpenAI, Anthropic, Mistral. Useful if we ever need multi-vendor LLM support without framework lock-in.

---

## Decision

Continue with the hand-built architecture. Adopt targeted libraries as needed for specific gaps. Do not introduce LangChain, LlamaIndex, or similar framework-level dependencies.
