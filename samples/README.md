# Code samples

Short excerpts from the private codebase, edited for reading. Each file opens with a note on the pattern it shows.

| File | Pattern |
|---|---|
| [sql_safety.py](sql_safety.py) | Model-generated SQL is checked by plain code before it runs: one statement, SELECT only, no forbidden keywords, no schema outside the allowlist |
| [agent_budget.py](agent_budget.py) | An agent turn tracks how much of the context window a call uses separately from what the turn costs |
| [reconciliation_rules.py](reconciliation_rules.py) | Data fixes as small, versioned, audited rules: deterministic first, model-assisted last, low confidence sent to review |
| [TrustEnvelope.cs](TrustEnvelope.cs) | Every MCP tool result carries its provenance and a trust class, and untrusted text can't leak into the summary |
