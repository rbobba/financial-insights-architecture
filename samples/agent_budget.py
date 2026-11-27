"""
Budget accounting for one agent turn.

Lightly edited from the production code. The agent's guardrails read this
ledger; they do no arithmetic of their own.

Pattern: one number can't answer two questions. The model is sent the earlier
steps of the turn again on every call, so adding up prompt sizes grows
quadratically while the real context grows linearly. A limit checked against
that running total stops the agent unpredictably. So the ledger keeps two
measures apart:

    context_tokens  how much of the model's window THIS call occupies.
                    A feasibility question. Not cumulative.
    billed_*        what the turn has actually cost. Cumulative, including
                    tokens spent inside tools, with cached input discounted.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgentBudgetLedger:
    """Tracks what an agent turn occupies and what it costs. One per turn."""

    # Feasibility: the most recent call's prompt size
    context_tokens: int = 0

    # Cost: cumulative across every call in this turn
    billed_input: int = 0
    billed_output: int = 0
    cached_input: int = 0
    nested_input: int = 0
    nested_output: int = 0

    iterations: int = 0
    start_time: float = field(default_factory=time.monotonic)

    @property
    def billed_total(self) -> int:
        """The honest cost of the turn, in tokens.

        Cached input is part of the prompt count but billed at a reduced rate;
        counting it at full price would make caching look like it saved nothing.
        """
        return (
            self.billed_input
            - self.cached_input
            + self.billed_output
            + self.nested_input
            + self.nested_output
        )

    @property
    def elapsed_seconds(self) -> float:
        return time.monotonic() - self.start_time

    def record_llm_call(self, usage: dict[str, Any]) -> None:
        """Record one model call.

        Input tokens REPLACE the context measure (this call's occupancy) and
        ADD to the billed measure (what the turn has cost). The same number
        legitimately feeds both; the mistake is checking the cumulative one
        against a limit shaped like the context window.
        """
        input_tokens = _int(usage.get("input_tokens"))
        self.context_tokens = input_tokens
        self.billed_input += input_tokens
        self.billed_output += _int(usage.get("output_tokens"))
        self.cached_input += _int(usage.get("cached_input_tokens"))

    def record_tool_call(self, usage: dict[str, Any]) -> None:
        """Charge the model spend that happened inside a tool.

        A SQL tool runs its own model calls. Without this, the turn's cost
        would be a flattering subset of the truth.
        """
        self.nested_input += _int(usage.get("input_tokens"))
        self.nested_output += _int(usage.get("output_tokens"))

    def start_iteration(self) -> None:
        self.iterations += 1

    def to_usage_dict(self) -> dict[str, Any]:
        return {
            "input_tokens": self.billed_input,
            "output_tokens": self.billed_output,
            "cached_input_tokens": self.cached_input,
            "nested_input_tokens": self.nested_input,
            "nested_output_tokens": self.nested_output,
            "billed_total_tokens": self.billed_total,
            "context_tokens": self.context_tokens,
            "iterations": self.iterations,
        }


def _int(value: Any) -> int:
    """Usage accounting must never be the thing that fails a request."""
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
