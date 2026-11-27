# Decision: no LangChain

**Status:** Accepted · February 2026

## Context

The platform needed model calls, versioned prompts, structured output parsing, multi-step pipelines, an agent loop with tools, retrieval, chat history and document chunking. Frameworks such as LangChain offer all of these behind one abstraction. By the time the question came up, a direct integration layer already covered each of them.

## Decision

Do not adopt LangChain or a similar framework. Keep the direct integration layer, with one adapter as the only code that calls the model SDK. Bring in a narrow library only when it closes a specific gap.

## Why

- **Debuggability.** A failed call surfaces the provider's own error, and the prompt is plain text you can print. There are no framework layers between the code and the call.
- **Smaller dependency surface.** Fewer transitive dependencies means fewer upgrades, conflicts and security advisories to track.
- **Stability.** Framework APIs change quickly. The platform's own interfaces change only when it needs them to.
- **Testability.** Pipelines and the agent loop are plain classes with explicit state, tested without mocking a framework.

## Consequences

- **Easier:** tracing a failure, swapping a model, and reviewing exactly what is sent to a model.
- **Harder:** the integration layer is mine to maintain. Adding a second model provider means extending the adapter; at larger scale, a model gateway takes on that job.
