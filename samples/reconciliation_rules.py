"""
The reconciliation rule framework.

Simplified from the production code: the persistence layer is replaced with a
plain audit record, and the engine is reduced to its ordering logic.

Pattern: every data-quality fix is a small, versioned rule.
  - scan() only reads and reports what it would change; apply() makes one change.
  - Every applied change writes an audit record: rule, version, old value, new value.
  - Deterministic rules run first. The one model-assisted rule runs last, on
    whatever the deterministic rules couldn't settle.
  - Low-confidence findings are not applied; they go to a person for review.
  - Running the rules twice changes nothing the second time.
"""

from __future__ import annotations

import enum
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from uuid import UUID


class RulePhase(int, enum.Enum):
    DETERMINISTIC = 1  # no model cost, fast, high confidence
    MODEL_ASSISTED = 2  # uses a model, slower, may need review


@dataclass(frozen=True)
class Finding:
    """One detected issue: a change described but not yet applied."""

    rule_name: str
    rule_version: str
    phase: RulePhase

    document_id: UUID | None = None
    transaction_id: UUID | None = None
    account_id: UUID | None = None

    field_changed: str | None = None
    old_value: str | None = None
    new_value: str | None = None
    detail: str | None = None

    confidence: Decimal = Decimal("1.0")
    auto_apply: bool = True  # False: a person reviews it first

    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AuditRecord:
    rule_name: str
    rule_version: str
    phase: int
    field_changed: str | None
    old_value: str | None
    new_value: str | None
    confidence: Decimal
    auto_applied: bool


class ReconciliationRule(ABC):
    @property
    @abstractmethod
    def rule_name(self) -> str: ...

    @property
    @abstractmethod
    def rule_version(self) -> str: ...

    @property
    @abstractmethod
    def phase(self) -> RulePhase: ...

    @abstractmethod
    async def scan(self, session: Any) -> list[Finding]:
        """Detect issues. Read-only and idempotent."""

    @abstractmethod
    async def apply(self, session: Any, finding: Finding) -> AuditRecord | None:
        """Apply one fix and return its audit record.

        Re-check that the issue still exists first, and return None if it
        doesn't. That check is what makes a second run a no-op.
        """

    def audit(self, finding: Finding) -> AuditRecord:
        return AuditRecord(
            rule_name=finding.rule_name,
            rule_version=finding.rule_version,
            phase=finding.phase.value,
            field_changed=finding.field_changed,
            old_value=finding.old_value,
            new_value=finding.new_value,
            confidence=finding.confidence,
            auto_applied=finding.auto_apply,
        )


async def run_rules(
    session: Any, rules: list[ReconciliationRule]
) -> tuple[list[AuditRecord], list[Finding]]:
    """Run every rule, deterministic phase first.

    Returns the audit records of the changes applied, and the findings held
    back for review.
    """
    applied: list[AuditRecord] = []
    for_review: list[Finding] = []
    for rule in sorted(rules, key=lambda r: r.phase):
        for finding in await rule.scan(session):
            if not finding.auto_apply:
                for_review.append(finding)
                continue
            record = await rule.apply(session, finding)
            if record is not None:
                applied.append(record)
    return applied, for_review
