from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
import json
from typing import Iterable
from uuid import UUID

from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    FinancialAccount, FinancialTransaction, FinancialTransactionStatus,
    LedgerBucket, LedgerDirection, LedgerEntry,
)
from app.services.audit import record_audit
from app.database.models import AuditAction, AuditEntity


class LedgerError(RuntimeError):
    pass


class LedgerImbalanceError(LedgerError):
    pass


class LedgerAlreadyPostedError(LedgerError):
    pass


class LedgerReversalError(LedgerError):
    pass


def ledger_request_fingerprint(
    *, workspace_id: UUID, user_id: UUID | None, actor_id: UUID | None,
    transaction_type: str, source: str, reference: str | None,
    lines: tuple[LedgerLine, ...],
    trade_id: UUID | None = None,
) -> str:
    payload = {
        "workspace_id": str(workspace_id),
        "user_id": str(user_id) if user_id else None,
        "actor_id": str(actor_id) if actor_id else None,
        "transaction_type": transaction_type,
        "source": source,
        "reference": reference,
        "trade_id": str(trade_id) if trade_id else None,
        "lines": [
            {"account_id": str(line.account_id), "asset": line.asset.upper(),
             "direction": line.direction.value, "bucket": line.bucket.value,
             "amount": str(line.amount), "memo": line.memo}
            for line in lines
        ],
    }
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class LedgerLine:
    account_id: UUID
    asset: str
    direction: LedgerDirection
    bucket: LedgerBucket
    amount: Decimal
    memo: str | None = None


@dataclass(frozen=True, slots=True)
class BalanceSnapshot:
    authoritative_balance: Decimal
    available_balance: Decimal
    reserved_balance: Decimal
    pending_balance: Decimal


class LedgerService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def account(self, *, workspace_id: UUID, user_id: UUID | None, asset: str, lock: bool = False, account_kind: str = "customer") -> FinancialAccount:
        normalized = asset.upper()
        statement = select(FinancialAccount).where(
            FinancialAccount.workspace_id == workspace_id,
            FinancialAccount.asset == normalized,
            FinancialAccount.account_kind == account_kind,
        )
        if account_kind == "customer":
            statement = statement.where(FinancialAccount.user_id == user_id)
        else:
            statement = statement.where(FinancialAccount.user_id.is_(None))
        if lock:
            statement = statement.with_for_update()
        account = await self.session.scalar(statement)
        if account is None:
            try:
                async with self.session.begin_nested():
                    account = FinancialAccount(workspace_id=workspace_id, user_id=user_id, asset=normalized, account_kind=account_kind)
                    self.session.add(account)
                    await self.session.flush()
            except IntegrityError:
                account = await self.session.scalar(statement)
                if account is None:
                    raise LedgerError("Financial account could not be created safely") from None
        return account

    async def post(
        self,
        *, workspace_id: UUID,
        user_id: UUID | None,
        actor_id: UUID | None,
        transaction_type: str,
        idempotency_key: str,
        source: str,
        lines: Iterable[LedgerLine],
        reference: str | None = None,
        provenance: dict | None = None,
        trade_id: UUID | None = None,
    ) -> FinancialTransaction:
        materialized = tuple(lines)
        if not materialized:
            raise LedgerError("A ledger transaction requires entries")
        fingerprint = ledger_request_fingerprint(
            workspace_id=workspace_id, user_id=user_id, actor_id=actor_id,
            transaction_type=transaction_type, source=source, reference=reference,
            lines=materialized, trade_id=trade_id,
        )
        existing = await self.session.scalar(select(FinancialTransaction).where(
            FinancialTransaction.workspace_id == workspace_id,
            FinancialTransaction.idempotency_key == idempotency_key,
        ))
        if existing is not None:
            stored = (existing.provenance or {}).get("_idempotency_fingerprint")
            if stored is None:
                raise LedgerError("Legacy ledger transaction requires safe reauthorization")
            if stored != fingerprint:
                raise LedgerError("Ledger idempotency key is bound to a different request payload")
            return existing
        assets = {line.asset.upper() for line in materialized}
        if len(assets) != 1 or any(line.amount <= 0 for line in materialized):
            raise LedgerError("Ledger entries must use one asset and positive Decimal amounts")
        debit = sum((line.amount for line in materialized if line.direction is LedgerDirection.DEBIT), Decimal("0"))
        credit = sum((line.amount for line in materialized if line.direction is LedgerDirection.CREDIT), Decimal("0"))
        if debit != credit:
            raise LedgerImbalanceError("Debit and credit totals must balance")
        try:
            async with self.session.begin_nested():
                transaction = FinancialTransaction(
                    workspace_id=workspace_id, user_id=user_id, actor_id=actor_id,
                    reference=reference, transaction_type=transaction_type,
                    status=FinancialTransactionStatus.POSTED.value, idempotency_key=idempotency_key,
                    source=source,
                    trade_id=trade_id,
                    provenance={**(provenance or {}), "_idempotency_fingerprint": fingerprint},
                    posted_at=datetime.now(UTC),
                )
                self.session.add(transaction)
                await self.session.flush()
                for line in materialized:
                    self.session.add(LedgerEntry(
                        transaction_id=transaction.id, financial_account_id=line.account_id,
                        asset=line.asset.upper(), direction=line.direction.value,
                        bucket=line.bucket.value, amount=line.amount, memo=line.memo,
                    ))
                audit_actor = actor_id or user_id
                if audit_actor is None:
                    raise LedgerError("Ledger transaction requires an audit actor")
                record_audit(self.session, actor_id=audit_actor, action=AuditAction.CREATE, entity=AuditEntity.ACCOUNT, entity_id=transaction.id, event_type="LEDGER_TRANSACTION_POSTED", event_metadata={"workspace_id": str(workspace_id), "transaction_type": transaction_type, "source": source})
                await self.session.flush()
        except IntegrityError:
            existing = await self.session.scalar(select(FinancialTransaction).where(
                FinancialTransaction.workspace_id == workspace_id,
                FinancialTransaction.idempotency_key == idempotency_key,
            ))
            if existing is not None:
                stored = (existing.provenance or {}).get("_idempotency_fingerprint")
                if stored is None:
                    raise LedgerError("Legacy ledger transaction requires safe reauthorization") from None
                if stored != fingerprint:
                    raise LedgerError("Ledger idempotency key is bound to a different request payload") from None
                return existing
            raise LedgerError("Ledger transaction could not be posted safely") from None
        return transaction

    async def balance(self, *, account_id: UUID, asset: str) -> BalanceSnapshot:
        signed = case((LedgerEntry.direction == LedgerDirection.CREDIT.value, LedgerEntry.amount), else_=-LedgerEntry.amount)
        result = await self.session.execute(select(
            func.coalesce(func.sum(signed), 0),
            func.coalesce(func.sum(case((LedgerEntry.bucket == LedgerBucket.RESERVED.value, signed), else_=0)), 0),
            func.coalesce(func.sum(case((LedgerEntry.bucket == LedgerBucket.PENDING.value, signed), else_=0)), 0),
        ).join(FinancialTransaction, FinancialTransaction.id == LedgerEntry.transaction_id).where(
            LedgerEntry.financial_account_id == account_id,
            LedgerEntry.asset == asset.upper(),
            FinancialTransaction.status.in_((FinancialTransactionStatus.POSTED.value, FinancialTransactionStatus.REVERSED.value)),
        ))
        total, reserved, pending = (Decimal(str(value or 0)) for value in result.one())
        return BalanceSnapshot(total, total - reserved - pending, reserved, pending)

    async def transfer_bucket(
        self, *, workspace_id: UUID, user_id: UUID, asset: str, amount: Decimal,
        source_bucket: LedgerBucket, target_bucket: LedgerBucket,
        transaction_type: str, idempotency_key: str, actor_id: UUID | None,
        reference: str,
    ) -> FinancialTransaction:
        if amount <= 0:
            raise LedgerError("Ledger transfer amount must be positive")
        account = await self.account(workspace_id=workspace_id, user_id=user_id, asset=asset, lock=True)
        snapshot = await self.balance(account_id=account.id, asset=asset)
        source_amount = {
            LedgerBucket.AVAILABLE: snapshot.available_balance,
            LedgerBucket.RESERVED: snapshot.reserved_balance,
            LedgerBucket.PENDING: snapshot.pending_balance,
        }[source_bucket]
        if amount > source_amount:
            raise LedgerError("Insufficient balance in ledger bucket")
        return await self.post(
            workspace_id=workspace_id, user_id=user_id, actor_id=actor_id,
            transaction_type=transaction_type, idempotency_key=idempotency_key,
            source="ledger", reference=reference,
            lines=(
                LedgerLine(account.id, asset, LedgerDirection.DEBIT, source_bucket, amount),
                LedgerLine(account.id, asset, LedgerDirection.CREDIT, target_bucket, amount),
            ),
        )

    async def reverse(self, *, transaction_id: UUID, actor_id: UUID, idempotency_key: str, reason: str, trade_id: UUID | None = None) -> FinancialTransaction:
        transaction = await self.session.get(FinancialTransaction, transaction_id)
        if transaction is None or transaction.status != FinancialTransactionStatus.POSTED.value:
            raise LedgerReversalError("Only a posted transaction can be reversed")
        entries = list((await self.session.scalars(select(LedgerEntry).where(LedgerEntry.transaction_id == transaction_id))).all())
        if not entries:
            raise LedgerReversalError("Posted transaction has no ledger entries")
        reversal = await self.post(
            workspace_id=transaction.workspace_id, user_id=transaction.user_id, actor_id=actor_id,
            transaction_type="reversal", idempotency_key=idempotency_key, source="ledger",
            reference=str(transaction.id), provenance={"reverses": str(transaction.id), "reason": reason},
            lines=tuple(LedgerLine(entry.financial_account_id, entry.asset, LedgerDirection.CREDIT if entry.direction == LedgerDirection.DEBIT.value else LedgerDirection.DEBIT, LedgerBucket(entry.bucket), entry.amount, "reversal") for entry in entries),
            trade_id=trade_id,
        )
        transaction.status = FinancialTransactionStatus.REVERSED.value
        await self.session.flush()
        return reversal
