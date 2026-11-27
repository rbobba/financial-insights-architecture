# Decision: models are chosen by evaluation

**Status:** Accepted · February 2026, revisited July 2026

## Context

Extraction must return long, structured output for large statements, and the model families that do this change and are retired every few months. A model choice made by preference or reputation goes stale, and it can't be defended when it does.

## Decision

Choose the model for each task (extraction, classification, questions, the agent) by running its golden dataset against the candidates. Treat model names as configuration. Keep a single adapter as the only code that touches the model SDK, so a model or SDK change stays in one place.

## How it played out

- **February 2026.** Long brokerage statements were being cut off because the extraction model's output limit was too small. The extraction model moved to one with a much larger output limit, and a JSON repair step was added for the occasional malformed response.
- **July 2026.** The model line in use was being retired, which forced an SDK migration. Because only one adapter calls the SDK, the port touched one file. Every task's model was re-selected by evaluation. On extraction, candidates across three price tiers scored the same, so the cheapest tier was chosen. The tie was also recorded as a finding about the evaluation: a golden set that can't separate the candidates needs sharper cases.

## Consequences

- **Easier:** model changes are configuration changes, and cost falls when a cheaper model holds up.
- **Harder:** every decision is only as good as its golden set. That is why the set grows from real user corrections, with a person deciding what is added.
