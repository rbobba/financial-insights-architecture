"""
Governance Reporter — automated compliance evidence generation.

Representative sample from Financial Insights Hub.
Demonstrates how governance reporting is derived from existing operational
data — no additional instrumentation needed.

Patterns:
  - Dataclass-based report structure (serializable, testable)
  - Parameterized SQL queries (tenant-scoped via RLS)
  - Threshold-based alerting (low confidence, high correction rate)
  - Markdown rendering for human-readable compliance reports
  - Framework mapping: ISO 42001, EU AI Act, NIST AI RMF, SOC 2

Implements:
  - ISO/IEC 42001 Annex A.7 (performance monitoring reports)
  - ISO/IEC 42001 Annex A.9 (record-keeping and documentation)
  - EU AI Act Art. 12 (automatic recording of events)
  - NIST AI RMF MANAGE 4.1 (AI risk treatment monitoring)
  - SOC 2 CC7.2 (monitoring of system components)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from fin_insights.governance.ai_system_registry import AISystemRegistry
from fin_insights.governance.model_card import ModelCardGenerator


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Report Data Structures
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@dataclass
class AIDecisionMetrics:
    """Aggregate metrics for AI decisions in a reporting period."""

    total_documents_processed: int = 0
    total_nlq_queries: int = 0
    total_extractions: int = 0
    avg_extraction_confidence: float = 0.0
    low_confidence_count: int = 0
    low_confidence_rate: float = 0.0


@dataclass
class HumanOversightMetrics:
    """Metrics for human oversight and correction activity."""

    total_corrections: int = 0
    unique_fields_corrected: int = 0
    correction_rate: float = 0.0         # corrections / total AI decisions
    corrections_by_field: dict[str, int] = field(default_factory=dict)
    auto_rules_created: int = 0          # Corrections that became auto-rules


@dataclass
class ModelUsageMetrics:
    """Model-level usage and cost metrics."""

    model_calls_by_surface: dict[str, int] = field(default_factory=dict)
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    estimated_total_cost_usd: float = 0.0


@dataclass
class ComplianceReport:
    """Complete governance compliance report for a reporting period.

    Contains all metrics needed for:
      - ISO 42001 management review
      - EU AI Act transparency obligations
      - NIST AI RMF quarterly assessment
      - SOC 2 Type II evidence
    """

    report_id: str
    generated_at: str
    period_start: str
    period_end: str
    total_ai_systems: int = 0
    systems_by_risk_level: dict[str, int] = field(default_factory=dict)
    decisions: AIDecisionMetrics = field(default_factory=AIDecisionMetrics)
    oversight: HumanOversightMetrics = field(default_factory=HumanOversightMetrics)
    usage: ModelUsageMetrics = field(default_factory=ModelUsageMetrics)
    alerts: list[str] = field(default_factory=list)

    def to_markdown(self) -> str:
        """Render compliance report as Markdown."""
        lines = [
            "# AI Governance Compliance Report",
            "",
            f"> **Report ID**: {self.report_id}",
            f"> **Period**: {self.period_start} to {self.period_end}",
            f"> **Generated**: {self.generated_at}",
            "",
            "---",
            "",
            "## 1. AI System Inventory",
            "",
            f"Total registered AI systems: **{self.total_ai_systems}**",
            "",
            "| Risk Level | Count |",
            "|------------|-------|",
        ]
        for level, count in sorted(self.systems_by_risk_level.items()):
            lines.append(f"| {level.upper()} | {count} |")

        lines.extend([
            "",
            "## 2. AI Decision Volume",
            "",
            f"| Metric | Value |",
            f"|--------|-------|",
            f"| Documents processed | {self.decisions.total_documents_processed} |",
            f"| NLQ queries | {self.decisions.total_nlq_queries} |",
            f"| Extractions | {self.decisions.total_extractions} |",
            f"| Avg extraction confidence | {self.decisions.avg_extraction_confidence:.2f} |",
            f"| Low confidence items | {self.decisions.low_confidence_count} |",
            f"| Low confidence rate | {self.decisions.low_confidence_rate:.1%} |",
            "",
            "## 3. Human Oversight",
            "",
            f"| Metric | Value |",
            f"|--------|-------|",
            f"| Total corrections | {self.oversight.total_corrections} |",
            f"| Correction rate | {self.oversight.correction_rate:.1%} |",
            f"| Auto-rules created | {self.oversight.auto_rules_created} |",
            "",
            "## 4. Model Usage & Cost",
            "",
            f"| Metric | Value |",
            f"|--------|-------|",
            f"| Total input tokens | {self.usage.total_input_tokens:,} |",
            f"| Total output tokens | {self.usage.total_output_tokens:,} |",
            f"| Estimated cost | ${self.usage.estimated_total_cost_usd:.2f} |",
            "",
        ])

        if self.alerts:
            lines.extend(["## 5. Alerts & Findings", ""])
            for alert in self.alerts:
                lines.append(f"- **ALERT**: {alert}")
            lines.append("")

        lines.extend([
            "---",
            f"*Auto-generated by GovernanceReporter — {self.generated_at}*",
        ])
        return "\n".join(lines)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Governance Reporter
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class GovernanceReporter:
    """Generates compliance reports from existing platform data.

    Queries processing_log, correction, transaction, and nlq_query_log
    tables to produce audit-grade metrics. No new data collection needed —
    governance reporting is derived from existing operational data.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._registry = AISystemRegistry()

    async def generate_report(
        self,
        start_date: date,
        end_date: date,
        user_id: str | None = None,
    ) -> ComplianceReport:
        """Generate a compliance report for the given period."""
        report_id = f"GOV-{start_date:%Y%m%d}-{end_date:%Y%m%d}"
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        systems = self._registry.list_all()
        risk_counts: dict[str, int] = {}
        for s in systems:
            level = s.risk_level.value
            risk_counts[level] = risk_counts.get(level, 0) + 1

        decisions = await self._query_decision_metrics(start_date, end_date, user_id)
        oversight = await self._query_oversight_metrics(start_date, end_date, user_id, decisions)
        usage = await self._query_usage_metrics(start_date, end_date, user_id)
        alerts = self._generate_alerts(decisions, oversight)

        return ComplianceReport(
            report_id=report_id,
            generated_at=now,
            period_start=start_date.isoformat(),
            period_end=end_date.isoformat(),
            total_ai_systems=len(systems),
            systems_by_risk_level=risk_counts,
            decisions=decisions,
            oversight=oversight,
            usage=usage,
            alerts=alerts,
        )

    async def _query_decision_metrics(
        self, start_date: date, end_date: date, user_id: str | None,
    ) -> AIDecisionMetrics:
        """Query processing_log and related tables for decision volume."""
        metrics = AIDecisionMetrics()
        user_clause, params = self._build_user_filter(user_id)
        params.update({"start": start_date, "end": end_date})

        result = await self._session.execute(
            text(
                f"SELECT COUNT(*) FROM finance.document "
                f"WHERE created_at >= :start AND created_at <= :end "
                f"AND deleted_at IS NULL {user_clause}"
            ),
            params,
        )
        metrics.total_documents_processed = result.scalar_one_or_none() or 0

        result = await self._session.execute(
            text(
                f"SELECT COUNT(*) FROM finance.nlq_query_log "
                f"WHERE created_at >= :start AND created_at <= :end "
                f"{user_clause}"
            ),
            params,
        )
        metrics.total_nlq_queries = result.scalar_one_or_none() or 0

        result = await self._session.execute(
            text(
                "SELECT COUNT(*) FROM finance.processing_log "
                "WHERE step_name = 'extraction' "
                "AND created_at >= :start AND created_at <= :end"
            ),
            params,
        )
        metrics.total_extractions = result.scalar_one_or_none() or 0

        result = await self._session.execute(
            text(
                f"SELECT "
                f"  AVG(overall_confidence), "
                f"  COUNT(*) FILTER (WHERE overall_confidence < 0.7), "
                f"  COUNT(*) "
                f"FROM finance.transaction "
                f"WHERE created_at >= :start AND created_at <= :end "
                f"AND deleted_at IS NULL {user_clause}"
            ),
            params,
        )
        row = result.one_or_none()
        if row and row[2]:
            metrics.avg_extraction_confidence = float(row[0] or 0)
            metrics.low_confidence_count = int(row[1] or 0)
            total_txns = int(row[2])
            metrics.low_confidence_rate = (
                metrics.low_confidence_count / total_txns if total_txns else 0
            )

        return metrics

    async def _query_oversight_metrics(
        self, start_date: date, end_date: date,
        user_id: str | None, decisions: AIDecisionMetrics,
    ) -> HumanOversightMetrics:
        """Query correction table for human oversight metrics."""
        metrics = HumanOversightMetrics()
        user_clause, params = self._build_user_filter(user_id)
        params.update({"start": start_date, "end": end_date})

        result = await self._session.execute(
            text(
                f"SELECT COUNT(*), COUNT(DISTINCT field_name) "
                f"FROM finance.correction "
                f"WHERE created_at >= :start AND created_at <= :end "
                f"{user_clause}"
            ),
            params,
        )
        row = result.one_or_none()
        if row:
            metrics.total_corrections = int(row[0] or 0)
            metrics.unique_fields_corrected = int(row[1] or 0)

        result = await self._session.execute(
            text(
                f"SELECT field_name, COUNT(*) "
                f"FROM finance.correction "
                f"WHERE created_at >= :start AND created_at <= :end "
                f"{user_clause} "
                f"GROUP BY field_name ORDER BY COUNT(*) DESC"
            ),
            params,
        )
        metrics.corrections_by_field = {
            row[0]: int(row[1]) for row in result.all()
        }

        result = await self._session.execute(
            text(
                "SELECT COUNT(*) FROM finance.merchant_category_rule "
                "WHERE created_at >= :start AND created_at <= :end"
            ),
            params,
        )
        metrics.auto_rules_created = result.scalar_one_or_none() or 0

        total_decisions = decisions.total_extractions + decisions.total_nlq_queries
        if total_decisions > 0:
            metrics.correction_rate = metrics.total_corrections / total_decisions

        return metrics

    async def _query_usage_metrics(
        self, start_date: date, end_date: date, user_id: str | None,
    ) -> ModelUsageMetrics:
        """Query processing_log for model usage and cost estimates."""
        metrics = ModelUsageMetrics()
        params = {"start": start_date, "end": end_date}

        result = await self._session.execute(
            text(
                "SELECT step_name, COUNT(*), "
                "  COALESCE(SUM((metadata->>'input_tokens')::int), 0), "
                "  COALESCE(SUM((metadata->>'output_tokens')::int), 0) "
                "FROM finance.processing_log "
                "WHERE created_at >= :start AND created_at <= :end "
                "GROUP BY step_name"
            ),
            params,
        )
        for row in result.all():
            step, count, in_tok, out_tok = row[0], int(row[1]), int(row[2]), int(row[3])
            metrics.model_calls_by_surface[step] = count
            metrics.total_input_tokens += in_tok
            metrics.total_output_tokens += out_tok

        metrics.estimated_total_cost_usd = (
            (metrics.total_input_tokens * 0.50 / 1_000_000)
            + (metrics.total_output_tokens * 2.00 / 1_000_000)
        )

        return metrics

    def _generate_alerts(
        self, decisions: AIDecisionMetrics, oversight: HumanOversightMetrics,
    ) -> list[str]:
        """Generate governance alerts based on threshold analysis."""
        alerts: list[str] = []

        if decisions.low_confidence_rate > 0.15:
            alerts.append(
                f"Low confidence rate ({decisions.low_confidence_rate:.1%}) "
                f"exceeds 15% threshold — investigate extraction quality"
            )

        if oversight.correction_rate > 0.15:
            alerts.append(
                f"Human correction rate ({oversight.correction_rate:.1%}) "
                f"exceeds 15% — AI accuracy may be degrading"
            )

        if decisions.avg_extraction_confidence < 0.7:
            alerts.append(
                f"Average extraction confidence ({decisions.avg_extraction_confidence:.2f}) "
                f"below 0.70 — prompt or model performance review needed"
            )

        return alerts

    @staticmethod
    def _build_user_filter(user_id: str | None) -> tuple[str, dict[str, str]]:
        """Build optional user_id WHERE clause for tenant scoping."""
        if user_id:
            return "AND user_id = :user_id", {"user_id": user_id}
        return "", {}
