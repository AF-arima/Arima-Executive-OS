from __future__ import annotations

from decimal import Decimal
from hashlib import sha256
import json
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    AuditAction, AuditEntity, LedgerBucket, LedgerDirection,
    FinancialAccount, FinancialTransaction, Portfolio, PortfolioPosition, SettledTrade, SettledTradeStatus, User, Workspace, WorkspaceMembership,
)
from app.schemas.trades import TradeCreate
from app.services.assets import normalize_asset, quantize_amount
from app.services.audit import record_audit
from app.services.identity import FinancialContextError, FinancialContextResolver
from app.services.ledger import LedgerLine, LedgerService


class TradeAccountingError(RuntimeError):
    pass


class TradeConflictError(TradeAccountingError):
    pass


class TradeAuthorizationError(TradeAccountingError):
    pass


def trade_fingerprint(*, target_user_id: UUID, workspace_id: UUID, data: TradeCreate, fee_asset: str) -> str:
    payload = {
        "target_user_id": str(target_user_id), "workspace_id": str(workspace_id),
        "side": data.side, "base_asset": data.base_asset, "quote_asset": data.quote_asset,
        "quantity": str(data.quantity), "price": str(data.price), "fee_asset": fee_asset,
        "fee_amount": str(data.fee_amount), "executed_at": data.executed_at.isoformat(),
        "external_execution_id": data.external_execution_id, "reason": data.reason,
    }
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class TradeAccountingService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.ledger = LedgerService(session)

    async def _target(self, *, actor: User, target_user_id: UUID) -> tuple[User, Workspace]:
        target = await self.session.get(User, target_user_id)
        if target is None:
            raise TradeAuthorizationError("Customer account not found")
        statement = (
            select(Workspace)
            .outerjoin(WorkspaceMembership, WorkspaceMembership.workspace_id == Workspace.id)
            .where((Workspace.owner_id == target_user_id) | (WorkspaceMembership.user_id == target_user_id))
            .order_by(Workspace.created_at)
        )
        workspaces = list((await self.session.scalars(statement)).all())
        if len(workspaces) != 1:
            raise TradeAuthorizationError("Authorized workspace selection is unavailable")
        workspace = workspaces[0]
        try:
            await FinancialContextResolver(self.session).resolve(actor=actor, workspace_id=workspace.id, account_id=target_user_id)
        except FinancialContextError as error:
            raise TradeAuthorizationError("Financial context is not authorized") from error
        return target, workspace

    async def _position(self, *, workspace: Workspace, user_id: UUID, asset: str) -> tuple[Portfolio, PortfolioPosition]:
        portfolio = await self.session.scalar(select(Portfolio).where(Portfolio.workspace_id == workspace.id, Portfolio.user_id == user_id).with_for_update())
        if portfolio is None:
            portfolio = Portfolio(workspace_id=workspace.id, user_id=user_id)
            self.session.add(portfolio)
            await self.session.flush()
        position = await self.session.scalar(select(PortfolioPosition).where(PortfolioPosition.portfolio_id == portfolio.id, PortfolioPosition.asset == asset).with_for_update())
        if position is None:
            position = PortfolioPosition(portfolio_id=portfolio.id, asset=asset, quantity=Decimal("0"), realized_pnl=Decimal("0"), unrealized_pnl=Decimal("0"))
            self.session.add(position)
            await self.session.flush()
        return portfolio, position

    async def _existing_position(self, *, workspace: Workspace, user_id: UUID, asset: str) -> PortfolioPosition | None:
        portfolio = await self.session.scalar(
            select(Portfolio)
            .where(Portfolio.workspace_id == workspace.id, Portfolio.user_id == user_id)
            .with_for_update()
        )
        if portfolio is None:
            return None
        return await self.session.scalar(
            select(PortfolioPosition)
            .where(PortfolioPosition.portfolio_id == portfolio.id, PortfolioPosition.asset == asset)
            .with_for_update()
        )

    async def _existing(self, *, workspace_id: UUID, key: str, fingerprint: str) -> SettledTrade | None:
        existing = await self.session.scalar(select(SettledTrade).where(SettledTrade.workspace_id == workspace_id, SettledTrade.idempotency_key == key))
        if existing is not None and existing.payload_fingerprint != fingerprint:
            raise TradeConflictError("Trade idempotency key is bound to a different payload")
        return existing

    async def record(self, *, actor: User, target_user_id: UUID, data: TradeCreate) -> SettledTrade:
        try:
            target, workspace = await self._target(actor=actor, target_user_id=target_user_id)
            base = normalize_asset(data.base_asset)
            quote = normalize_asset(data.quote_asset)
            if base == quote:
                raise TradeAccountingError("Base and quote assets must differ")
            if data.fee_amount < 0:
                raise TradeAccountingError("Fee cannot be negative")
            fingerprint = trade_fingerprint(target_user_id=target.id, workspace_id=workspace.id, data=data, fee_asset=quote)
            existing = await self._existing(workspace_id=workspace.id, key=data.idempotency_key, fingerprint=fingerprint)
            if existing is not None:
                return existing
            quantity = quantize_amount(data.quantity)
            price = quantize_amount(data.price)
            fee = quantize_amount(data.fee_amount)
            quote_value = quantize_amount(quantity * price)
            if quote_value <= 0 or fee < 0 or (data.side == "sell" and fee >= quote_value):
                raise TradeAccountingError("Trade amounts are invalid")
            portfolio, position = await self._position(workspace=workspace, user_id=target.id, asset=base)
            before_quantity = quantize_amount(position.quantity)
            before_average = quantize_amount(position.average_cost) if position.average_cost is not None else None
            before_realized = quantize_amount(position.realized_pnl)
            if before_quantity > 0 and before_average is None:
                raise TradeAccountingError("Existing position has no average cost")
            customer_quote = await self.ledger.account(workspace_id=workspace.id, user_id=target.id, asset=quote, lock=True)
            customer_base = await self.ledger.account(workspace_id=workspace.id, user_id=target.id, asset=base, lock=True)
            clearing_quote = await self.ledger.account(workspace_id=workspace.id, user_id=None, asset=quote, lock=True, account_kind="clearing")
            clearing_base = await self.ledger.account(workspace_id=workspace.id, user_id=None, asset=base, lock=True, account_kind="clearing")
            quote_total = quote_value + fee if data.side == "buy" else quote_value - fee
            after_average: Decimal | None
            if data.side == "buy":
                if (await self.ledger.balance(account_id=customer_quote.id, asset=quote)).available_balance < quote_total:
                    raise TradeAccountingError("Insufficient quote balance")
                after_quantity = before_quantity + quantity
                after_average = quantize_amount(((before_quantity * (before_average or Decimal("0"))) + quote_value + fee) / after_quantity)
                after_realized = before_realized
                quote_lines = (LedgerLine(customer_quote.id, quote, LedgerDirection.DEBIT, LedgerBucket.AVAILABLE, quote_total, "manual trade buy"), LedgerLine(clearing_quote.id, quote, LedgerDirection.CREDIT, LedgerBucket.AVAILABLE, quote_total, "manual trade buy"))
                base_lines = (LedgerLine(clearing_base.id, base, LedgerDirection.DEBIT, LedgerBucket.AVAILABLE, quantity, "manual trade buy"), LedgerLine(customer_base.id, base, LedgerDirection.CREDIT, LedgerBucket.AVAILABLE, quantity, "manual trade buy"))
            else:
                if before_quantity < quantity or (await self.ledger.balance(account_id=customer_base.id, asset=base)).available_balance < quantity:
                    raise TradeAccountingError("Insufficient base position or balance")
                if before_average is None and before_quantity > 0:
                    raise TradeAccountingError("Existing position has no average cost")
                after_quantity = before_quantity - quantity
                after_average = before_average if after_quantity > 0 else None
                after_realized = before_realized + (quote_value - fee) - (quantity * (before_average or Decimal("0")))
                quote_lines = (LedgerLine(clearing_quote.id, quote, LedgerDirection.DEBIT, LedgerBucket.AVAILABLE, quote_total, "manual trade sell"), LedgerLine(customer_quote.id, quote, LedgerDirection.CREDIT, LedgerBucket.AVAILABLE, quote_total, "manual trade sell"))
                base_lines = (LedgerLine(customer_base.id, base, LedgerDirection.DEBIT, LedgerBucket.AVAILABLE, quantity, "manual trade sell"), LedgerLine(clearing_base.id, base, LedgerDirection.CREDIT, LedgerBucket.AVAILABLE, quantity, "manual trade sell"))
            trade = SettledTrade(id=uuid4(), workspace_id=workspace.id, target_user_id=target.id, founder_actor_id=actor.id, side=data.side, base_asset=base, quote_asset=quote, quantity=quantity, price=price, quote_value=quote_value, fee_asset=quote, fee_amount=fee, executed_at=data.executed_at, status=SettledTradeStatus.RECORDED.value, external_execution_id=data.external_execution_id, idempotency_key=data.idempotency_key, payload_fingerprint=fingerprint, reason=data.reason, position_before_quantity=before_quantity, position_before_average_cost=before_average, position_before_realized_pnl=before_realized, position_after_quantity=after_quantity, position_after_average_cost=after_average, position_after_realized_pnl=after_realized)
            self.session.add(trade)
            await self.session.flush()
            await self.ledger.post(workspace_id=workspace.id, user_id=target.id, actor_id=actor.id, transaction_type="trade_quote_leg", idempotency_key=f"trade:{trade.id}:quote", source="manual_trade", reference=str(trade.id), provenance={"trade_id": str(trade.id), "fee_asset": quote, "fee_amount": str(fee)}, lines=quote_lines, trade_id=trade.id)
            await self.ledger.post(workspace_id=workspace.id, user_id=target.id, actor_id=actor.id, transaction_type="trade_base_leg", idempotency_key=f"trade:{trade.id}:base", source="manual_trade", reference=str(trade.id), provenance={"trade_id": str(trade.id)}, lines=base_lines, trade_id=trade.id)
            position.quantity, position.average_cost, position.realized_pnl = after_quantity, after_average, after_realized
            record_audit(self.session, actor_id=actor.id, action=AuditAction.CREATE, entity=AuditEntity.TRADE, entity_id=trade.id, event_type="FOUNDER_TRADE_RECORDED", event_metadata={"target_user_id": str(target.id), "workspace_id": str(workspace.id), "side": data.side, "base_asset": base, "quote_asset": quote})
            await self.session.commit()
            return trade
        except Exception:
            await self.session.rollback()
            raise

    async def reverse(self, *, actor: User, target_user_id: UUID, trade_id: UUID, idempotency_key: str, reason: str) -> SettledTrade:
        try:
            target, workspace = await self._target(actor=actor, target_user_id=target_user_id)
            original = await self.session.scalar(select(SettledTrade).where(SettledTrade.id == trade_id, SettledTrade.workspace_id == workspace.id, SettledTrade.target_user_id == target.id).with_for_update())
            if original is None:
                raise TradeAuthorizationError("Trade not found")
            fingerprint = sha256(json.dumps({"trade_id": str(trade_id), "reason": reason}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            existing = await self._existing(workspace_id=workspace.id, key=idempotency_key, fingerprint=fingerprint)
            if existing is not None:
                return existing
            if original.status != SettledTradeStatus.RECORDED.value or original.reversal_of_id is not None:
                raise TradeConflictError("Trade is already reversed or is not reversible")
            position = await self._existing_position(workspace=workspace, user_id=target.id, asset=original.base_asset)
            if position is None:
                raise TradeConflictError("Trade position is unavailable for reversal")
            if quantize_amount(position.quantity) != quantize_amount(original.position_after_quantity) or position.average_cost != original.position_after_average_cost or quantize_amount(position.realized_pnl) != quantize_amount(original.position_after_realized_pnl):
                raise TradeConflictError("Trade can only be reversed from its current recorded position state")
            if original.position_before_quantity < 0:
                raise TradeConflictError("Trade reversal would create a negative position")

            inverse_quote_amount = quantize_amount(
                original.quote_value + original.fee_amount
                if original.side == "buy"
                else original.quote_value - original.fee_amount
            )
            if inverse_quote_amount <= 0:
                raise TradeConflictError("Trade reversal has an invalid quote amount")
            affected_asset = original.base_asset if original.side == "buy" else original.quote_asset
            affected_amount = original.quantity if original.side == "buy" else inverse_quote_amount
            affected_account = await self.session.scalar(
                select(FinancialAccount)
                .where(
                    FinancialAccount.workspace_id == workspace.id,
                    FinancialAccount.user_id == target.id,
                    FinancialAccount.asset == affected_asset,
                    FinancialAccount.account_kind == "customer",
                )
                .with_for_update()
            )
            if affected_account is None:
                raise TradeConflictError("Trade reversal balance account is unavailable")
            balance = await self.ledger.balance(account_id=affected_account.id, asset=affected_asset)
            if balance.available_balance < affected_amount:
                raise TradeConflictError("Trade reversal would create a negative available balance")
            if original.side == "buy" and quantize_amount(position.quantity - original.quantity) < 0:
                raise TradeConflictError("Trade reversal would create a negative position")
            reversal = SettledTrade(id=uuid4(), workspace_id=workspace.id, target_user_id=target.id, founder_actor_id=actor.id, reversal_of_id=original.id, side=original.side, base_asset=original.base_asset, quote_asset=original.quote_asset, quantity=original.quantity, price=original.price, quote_value=original.quote_value, fee_asset=original.fee_asset, fee_amount=original.fee_amount, executed_at=original.executed_at, status=SettledTradeStatus.RECORDED.value, external_execution_id=original.external_execution_id, idempotency_key=idempotency_key, payload_fingerprint=fingerprint, reason=reason, position_before_quantity=original.position_after_quantity, position_before_average_cost=original.position_after_average_cost, position_before_realized_pnl=original.position_after_realized_pnl, position_after_quantity=original.position_before_quantity, position_after_average_cost=original.position_before_average_cost, position_after_realized_pnl=original.position_before_realized_pnl)
            self.session.add(reversal)
            await self.session.flush()
            transactions = list((await self.session.scalars(select(FinancialTransaction).where(FinancialTransaction.trade_id == original.id).order_by(FinancialTransaction.created_at))).all())
            if len(transactions) != 2:
                raise TradeConflictError("Trade ledger legs are incomplete")
            for transaction in transactions:
                await self.ledger.reverse(transaction_id=transaction.id, actor_id=actor.id, idempotency_key=f"trade:{reversal.id}:reverse:{transaction.id}", reason=reason, trade_id=reversal.id)
            position.quantity, position.average_cost, position.realized_pnl = original.position_before_quantity, original.position_before_average_cost, original.position_before_realized_pnl
            original.status = SettledTradeStatus.REVERSED.value
            record_audit(self.session, actor_id=actor.id, action=AuditAction.STATUS_CHANGE, entity=AuditEntity.TRADE, entity_id=reversal.id, event_type="FOUNDER_TRADE_REVERSED", event_metadata={"target_user_id": str(target.id), "workspace_id": str(workspace.id), "original_trade_id": str(original.id)})
            await self.session.commit()
            return reversal
        except Exception:
            await self.session.rollback()
            raise
