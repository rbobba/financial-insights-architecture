"""
Correction Service — human-in-the-loop feedback that closes the accuracy loop.

Representative sample from Financial Insights Hub.
Demonstrates the correction pipeline: validate → update → audit → auto-rule.

Patterns:
  - Immutable audit trail (Correction table — append-only)
  - Auto-rule generation (corrections become merchant_category_rules)
  - Blast-radius-aware bulk updates (apply_to_all with description matching)
  - Confidence promotion (corrected items leave the needs-review queue)
  - Category resolution (find-or-create from free-text input)

This is Loop 1 from SPEC-14: Correction → Auto-Rule Pipeline.
"""

from __future__ import annotations

import re
from decimal import Decimal
from uuid import UUID

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from fin_insights.adapters.db.category import Category
from fin_insights.adapters.db.correction import Correction
from fin_insights.adapters.db.merchant_alias import MerchantAlias
from fin_insights.adapters.db.merchant_category_rule import MerchantCategoryRule
from fin_insights.adapters.db.party import Party
from fin_insights.adapters.db.transaction import Transaction
from fin_insights.adapters.db.enums import (
    ConfidenceLevel,
    MoneyFlowType,
    PartyRole,
    ReportingStatus,
    TransactionType,
)
from fin_insights.api.schemas.corrections import (
    CategoryCorrectionRequest,
    CategoryCorrectionResponse,
    MerchantCorrectionRequest,
    MerchantCorrectionResponse,
    MoneyFlowCorrectionRequest,
    MoneyFlowCorrectionResponse,
    TransactionTypeCorrectionRequest,
    TransactionTypeCorrectionResponse,
    ReportingStatusCorrectionRequest,
    ReportingStatusCorrectionResponse,
    UndoCorrectionResponse,
)
from fin_insights.services.category_resolution_service import CategoryResolutionService
from fin_insights.shared.exceptions import DocumentNotFoundError

logger = structlog.get_logger(__name__)


class CorrectionService:
    """Handles user corrections and auto-creates category rules.

    Supports five correction types:
      - Category: reassign transaction category + auto-create merchant rule
      - Merchant: reassign merchant + auto-create alias for future matching
      - Money flow: correct income/expense/transfer classification
      - Transaction type: correct credit/debit designation
      - Reporting status: toggle inclusion/exclusion from reports

    Each correction:
      1. Updates the transaction field
      2. Records an immutable Correction audit row
      3. Auto-creates rules/aliases so future uploads reflect the correction
      4. Optionally applies to all matching sibling transactions (blast-radius)
      5. Promotes confidence to 0.95 (exits needs-review queue)
    """

    # Regex to strip confirmation/reference numbers for description matching
    _CONFIRMATION_RE = re.compile(
        r"[\s;]*(?:Confirmation|Conf|Ref|Reference|ID)[#:\s]*\S+[\s;]*",
        re.IGNORECASE,
    )

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @staticmethod
    def _promote_confidence(txn: Transaction) -> None:
        """Promote confidence after a manual correction.

        When a user manually corrects any field, the transaction
        is considered human-verified. Bump confidence to 0.95
        (high) so it no longer appears in the needs-review queue.
        """
        txn.confidence = Decimal("0.95")
        txn.confidence_level = ConfidenceLevel.HIGH

    @classmethod
    def _description_prefix(cls, description: str) -> str:
        """Build a SQL LIKE pattern from a transaction description.

        Replaces variable confirmation/reference numbers with '%' wildcard
        so we can match all sibling transactions sharing the same template.

        Examples:
          'Mobile transfer to CHK 9999 Confirmation# abc;'
              → 'Mobile transfer to CHK 9999%'
          'Online Banking Transfer Conf# xyz; Smith'
              → 'Online Banking Transfer%Smith'
        """
        safe = description.replace("%", r"\%").replace("_", r"\_")
        pattern = cls._CONFIRMATION_RE.sub("%", safe).strip().rstrip(";")
        while "%%" in pattern:
            pattern = pattern.replace("%%", "%")
        return pattern if pattern and pattern != "%" else safe

    async def correct_category(
        self,
        transaction_id: UUID,
        user_id: UUID,
        body: CategoryCorrectionRequest,
    ) -> CategoryCorrectionResponse:
        """Correct a transaction's category and auto-create a merchant rule.

        Steps:
          1. Load transaction (with merchant relationship)
          2. Resolve target category (by ID or free-text)
          3. Update the transaction
          4. Record correction audit row
          5. Auto-create/update merchant_category_rule
          6. Optionally apply to all sibling transactions (blast-radius)
        """
        log = logger.bind(
            transaction_id=str(transaction_id),
            user_id=str(user_id),
            new_category_id=str(body.category_id) if body.category_id else None,
            category_text=body.category_text,
        )

        # 1. Load transaction (eagerly load merchant for rule pattern extraction)
        result = await self._session.execute(
            select(Transaction)
            .options(selectinload(Transaction.merchant))
            .where(Transaction.id == transaction_id)
        )
        txn = result.scalar_one_or_none()
        if txn is None or txn.deleted_at is not None:
            raise DocumentNotFoundError(
                f"Transaction {transaction_id} not found",
                document_id=str(transaction_id),
            )

        # 2. Resolve target category — either by ID or free-text
        category_created = False
        if body.category_id:
            target_cat = await self._session.get(Category, body.category_id)
            if target_cat is None:
                raise DocumentNotFoundError(
                    f"Category {body.category_id} not found",
                    document_id=str(body.category_id),
                )
        elif body.category_text:
            resolver = CategoryResolutionService(self._session)
            target_cat, category_created = await resolver.resolve(
                body.category_text, user_id=user_id,
            )
        else:
            raise ValueError("Either category_id or category_text must be provided")

        old_category_name = txn.category_text or "None"
        old_category_id = txn.category_id
        new_category_name = target_cat.name

        if txn.category_id == target_cat.id:
            return CategoryCorrectionResponse(
                transaction_id=transaction_id,
                old_category=old_category_name,
                new_category=new_category_name,
                message="Category already correct — no change needed",
            )

        # 3. Update the transaction
        txn.category_id = target_cat.id
        txn.category_text = new_category_name
        self._promote_confidence(txn)

        # 4. Record correction audit trail (immutable, append-only)
        correction = Correction(
            user_id=user_id,
            entity_type="transaction",
            entity_id=transaction_id,
            field_name="category_id",
            old_value={"category_id": str(old_category_id), "category_text": old_category_name},
            new_value={"category_id": str(target_cat.id), "category_text": new_category_name},
            reason=body.reason,
            correction_source="manual",
            created_by="user",
        )
        self._session.add(correction)

        # 5. Auto-create merchant_category_rule (future uploads auto-categorize)
        rule_created = False
        rule_pattern = None
        merchant_name = None
        merchant_party_id = None
        if txn.merchant:
            merchant_name = txn.merchant.display_name or txn.merchant.canonical_name
            merchant_party_id = txn.merchant_party_id

        pattern = self._extract_rule_pattern(merchant_name, txn.description)
        if pattern or merchant_party_id:
            rule = await self._upsert_merchant_rule(
                user_id=user_id,
                category_id=target_cat.id,
                merchant_party_id=merchant_party_id,
                pattern=pattern,
            )
            if rule:
                rule_created = True
                rule_pattern = pattern or (merchant_name if merchant_party_id else None)

        await self._session.flush()

        # 6. Blast-radius: optionally apply to all matching transactions
        transactions_updated = 1
        if body.apply_to_all:
            _bulk_values = dict(
                category_id=target_cat.id,
                category_text=new_category_name,
                confidence=Decimal("0.95"),
                confidence_level=ConfidenceLevel.HIGH,
            )
            if txn.merchant_party_id:
                stmt = (
                    update(Transaction)
                    .where(
                        Transaction.merchant_party_id == txn.merchant_party_id,
                        Transaction.id != transaction_id,
                        Transaction.deleted_at.is_(None),
                    )
                    .values(**_bulk_values)
                )
                result = await self._session.execute(stmt)
                transactions_updated += result.rowcount
            elif txn.description:
                prefix = self._description_prefix(txn.description)
                stmt = (
                    update(Transaction)
                    .where(
                        Transaction.description.ilike(f"{prefix}%"),
                        Transaction.id != transaction_id,
                        Transaction.deleted_at.is_(None),
                    )
                    .values(**_bulk_values)
                )
                result = await self._session.execute(stmt)
                transactions_updated += result.rowcount

        log.info(
            "category_corrected",
            old=old_category_name,
            new=new_category_name,
            category_created=category_created,
            rule_created=rule_created,
            rule_pattern=rule_pattern,
            transactions_updated=transactions_updated,
        )

        return CategoryCorrectionResponse(
            transaction_id=transaction_id,
            correction_id=correction.id,
            old_category=old_category_name,
            new_category=new_category_name,
            category_created=category_created,
            rule_created=rule_created,
            rule_pattern=rule_pattern,
            transactions_updated=transactions_updated,
            message=(
                f"Category corrected: '{old_category_name}' → '{new_category_name}'"
                + (f". New category '{new_category_name}' created" if category_created else "")
                + (f". Rule created for pattern '{rule_pattern}'" if rule_created else "")
                + (f". {transactions_updated} transaction(s) updated" if transactions_updated > 1 else "")
            ),
        )

    # ── Additional correction methods ────────────────────────────────
    #
    # The full service implements five correction types:
    #   correct_merchant()       — reassign merchant + create alias
    #   correct_money_flow()     — correct income/expense/transfer
    #   correct_transaction_type() — correct credit/debit
    #   correct_reporting_status() — toggle report inclusion
    #   undo_correction()        — revert to previous value
    #
    # Each follows the same pattern:
    #   validate → update → audit → auto-rule → optional blast-radius
    #
    # See the full implementation in the private repository.
