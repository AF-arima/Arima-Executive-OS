from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
import json
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    AuditAction,
    AuditEntity,
    FinancialTransaction,
    LedgerBucket,
    LedgerDirection,
    LedgerEntry,
    User,
    Workspace,
    WorkspaceMembership,
)
from app.schemas.deposits import DepositCreate
from app.services.assets import normalize_asset, quantize_amount
from app.services.audit import record_audit
from app.services.identity import FinancialContextError, FinancialContextResolver
from app.services.ledger import LedgerLine, LedgerService


class DepositAccountingError(RuntimeError):
    pass


class DepositConflictError(DepositAccountingError):
    pass


class DepositAuthorizationError(DepositAccountingError):
    pass


@dataclass(frozen=True, slots=True)
class DepositResult:
    id: UUID
    workspace_id: UUID
    target_user_id: UUID
    founder_actor_id: UUID
    asset: str
    amount: Decimal
    reference: str
    reason: str
    idempotency_key: str
    financial_transaction_id: UUID
    financial_account_id: UUID


def deposit_fingerprint(*, target_user_id: UUID, workspace_id: UUID, data: DepositCreate, asset: str) -> str:
    payload = {
        "target_user_id": str(target_user_id),
        "workspace_id": str(workspace_id),
        "asset": asset,
        "amount": str(data.amount),
        "reference": data.reference,
        "reason": data.reason,
        "provenance": "FOUNDER_MANUAL_DEPOSIT",
    }
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class DepositAccountingService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.ledger = LedgerService(session)

    async def _target(self, *, actor: User, target_user_id: UUID) -> tuple[User, Workspace]:
        target = await self.session.get(User, target_user_id)
        if target is None:
            raise DepositAuthorizationError("Customer account not found")
        workspaces = list((await self.session.scalars(
            select(Workspace)
            .outerjoin(WorkspaceMembership, WorkspaceMembership.workspace_id == Workspace.id)
            .where((Workspace.owner_id == target_user_id) | (WorkspaceMembership.user_id == target_user_id))
            .order_by(Workspace.created_at)
        )).all())
        if len(workspaces) != 1:
            raise DepositAuthorizationError("Authorized workspace selection is unavailable")
        workspace = workspaces[0]
        try:
            await FinancialContextResolver(self.session).resolve(
                actor=actor,
                workspace_id=workspace.id,
                account_id=target_user_id,
            )
        except FinancialContextError as error:
            raise DepositAuthorizationError("Financial context is not authorized") from error
        return target, workspace

    async def _existing(self, *, workspace_id: UUID, key: str, fingerprint: str) -> DepositResult | None:
        transaction = await self.session.scalar(select(FinancialTransaction).where(
            FinancialTransaction.workspace_id == workspace_id,
            FinancialTransaction.idempotency_key == key,
        ))
        if transaction is None:
            return None
        stored = (transaction.provenance or {}).get("deposit_payload_fingerprint")
        if stored != fingerprint:
            raise DepositConflictError("Deposit idempotency key is bound to a different payload")
        entries = list((await self.session.scalars(
            select(LedgerEntry).where(LedgerEntry.transaction_id == transaction.id)
        )).all())
        customer_entry = next((entry for entry in entries if entry.direction == LedgerDirection.CREDIT.value), None)
        if customer_entry is None or transaction.user_id is None:
            raise DepositConflictError("Existing deposit ledger transaction is incomplete")
        metadata = (transaction.provenance or {}).get("deposit") or {}
        return DepositResult(
            id=transaction.id,
            workspace_id=transaction.workspace_id,
            target_user_id=transaction.user_id,
            founder_actor_id=transaction.actor_id or transaction.user_id,
            asset=customer_entry.asset,
            amount=customer_entry.amount,
            reference=transaction.reference or "",
            reason=str(metadata.get("reason", "")),
            idempotency_key=transaction.idempotency_key,
            financial_transaction_id=transaction.id,
            financial_account_id=customer_entry.financial_account_id,
        )

    async def record(self, *, actor: User, target_user_id: UUID, data: DepositCreate) -> DepositResult:
        try:
            target, workspace = await self._target(actor=actor, target_user_id=target_user_id)
            asset = normalize_asset(data.asset)
            amount = quantize_amount(data.amount)
            if amount <= 0:
                raise DepositAccountingError("Deposit amount must be positive")
            fingerprint = deposit_fingerprint(
                target_user_id=target.id,
                workspace_id=workspace.id,
                data=data,
                asset=asset,
            )
            existing = await self._existing(
                workspace_id=workspace.id,
                key=data.idempotency_key,
                fingerprint=fingerprint,
            )
            if existing is not None:
                return existing

            # The target/idempotency checks are read-only. Start the financial
            # mutation from a clean transaction so account creation and ledger
            # posting share one rollback boundary.
            target_id = target.id
            workspace_id = workspace.id
            actor_id = actor.id
            await self.session.rollback()
            await self.session.begin()
            customer = await self.ledger.account_in_transaction(
                workspace_id=workspace_id,
                user_id=target_id,
                asset=asset,
                lock=True,
            )
            clearing = await self.ledger.account_in_transaction(
                workspace_id=workspace_id,
                user_id=None,
                asset=asset,
                lock=True,
                account_kind="clearing",
            )
            transaction = await self.ledger.post(
                workspace_id=workspace_id,
                user_id=target_id,
                actor_id=actor_id,
                transaction_type="deposit",
                idempotency_key=data.idempotency_key,
                source="founder_manual_deposit",
                reference=data.reference,
                provenance={
                    "deposit_payload_fingerprint": fingerprint,
                    "deposit": {
                        "provenance": "FOUNDER_MANUAL_DEPOSIT",
                        "reason": data.reason,
                        "reference": data.reference,
                        "source_account_kind": "clearing",
                    },
                },
                lines=(
                    LedgerLine(clearing.id, asset, LedgerDirection.DEBIT, LedgerBucket.AVAILABLE, amount, "founder manual deposit source"),
                    LedgerLine(customer.id, asset, LedgerDirection.CREDIT, LedgerBucket.AVAILABLE, amount, "founder manual deposit customer credit"),
                ),
            )
            record_audit(
                self.session,
                actor_id=actor_id,
                action=AuditAction.CREATE,
                entity=AuditEntity.ACCOUNT,
                entity_id=transaction.id,
                event_type="FOUNDER_MANUAL_DEPOSIT_RECORDED",
                event_metadata={
                    "target_user_id": str(target_id),
                    "workspace_id": str(workspace_id),
                    "financial_transaction_id": str(transaction.id),
                    "financial_account_id": str(customer.id),
                    "asset": asset,
                    "amount": str(amount),
                    "reference": data.reference,
                    "reason": data.reason,
                    "provenance": "FOUNDER_MANUAL_DEPOSIT",
                },
            )
            await self.session.commit()
            return DepositResult(
                id=transaction.id,
                workspace_id=workspace_id,
                target_user_id=target_id,
                founder_actor_id=actor_id,
                asset=asset,
                amount=amount,
                reference=data.reference,
                reason=data.reason,
                idempotency_key=data.idempotency_key,
                financial_transaction_id=transaction.id,
                financial_account_id=customer.id,
            )
        except Exception:
            await self.session.rollback()
            raise
