from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    FinancialAccount, FinancialTransaction, LedgerEntry, Portfolio, PortfolioPosition,
    User, Workspace, WorkspaceMembership,
)
from app.services.ledger import BalanceSnapshot, LedgerService
from app.database.models import AuditAction, AuditEntity
from app.services.audit import record_audit
from app.services.permissions import has_founder_control_access
from app.services.identity import FinancialContextResolver, FinancialContextError


class PortfolioAuthorizationError(LookupError):
    pass


@dataclass(frozen=True, slots=True)
class PortfolioSummary:
    portfolio_id: UUID
    workspace_id: UUID
    user_id: UUID
    balances: list[tuple[str, BalanceSnapshot]]
    positions: list[PortfolioPosition]
    transactions: list[FinancialTransaction]


class PortfolioService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.ledger = LedgerService(session)

    async def workspace_for(self, user_id: UUID) -> Workspace:
        statement = select(Workspace).outerjoin(WorkspaceMembership, WorkspaceMembership.workspace_id == Workspace.id).where(
            (Workspace.owner_id == user_id) | (WorkspaceMembership.user_id == user_id)
        ).order_by(Workspace.created_at)
        workspaces = list((await self.session.scalars(statement)).all())
        if not workspaces:
            raise LookupError("Authorized workspace is unavailable")
        if len(workspaces) != 1:
            raise LookupError("Authorized workspace selection is ambiguous")
        return workspaces[0]

    async def summary(self, *, user_id: UUID, actor_id: UUID, workspace_id: UUID | None = None, require_authoritative_context: bool = True) -> PortfolioSummary:
        actor = await self.session.get(User, actor_id)
        target = await self.session.get(User, user_id)
        if actor is None or target is None:
            raise PortfolioAuthorizationError("Authorized portfolio identity is unavailable")
        if actor.id != target.id and not has_founder_control_access(actor):
            raise PortfolioAuthorizationError("Actor is not authorized for this portfolio")
        if workspace_id is None:
            workspace = await self.workspace_for(user_id)
        else:
            workspace = await self.session.scalar(select(Workspace).outerjoin(WorkspaceMembership, WorkspaceMembership.workspace_id == Workspace.id).where(
                Workspace.id == workspace_id, (Workspace.owner_id == user_id) | (WorkspaceMembership.user_id == user_id)
            ))
        if workspace is None:
            raise LookupError("Portfolio workspace not found")
        if require_authoritative_context:
            try:
                await FinancialContextResolver(self.session).resolve(
                    actor=actor, workspace_id=workspace.id, account_id=user_id,
                )
            except FinancialContextError as error:
                raise PortfolioAuthorizationError(str(error)) from error
        portfolio = await self.session.scalar(select(Portfolio).where(Portfolio.workspace_id == workspace.id, Portfolio.user_id == user_id))
        if portfolio is None:
            portfolio = Portfolio(workspace_id=workspace.id, user_id=user_id)
            self.session.add(portfolio)
            await self.session.flush()
        accounts = list((await self.session.scalars(select(FinancialAccount).where(FinancialAccount.workspace_id == workspace.id, FinancialAccount.user_id == user_id).order_by(FinancialAccount.asset))).all())
        balances = [(account.asset, await self.ledger.balance(account_id=account.id, asset=account.asset)) for account in accounts]
        positions = list((await self.session.scalars(select(PortfolioPosition).where(PortfolioPosition.portfolio_id == portfolio.id).order_by(PortfolioPosition.asset))).all())
        transaction_ids = select(LedgerEntry.transaction_id).join(FinancialAccount, FinancialAccount.id == LedgerEntry.financial_account_id).where(FinancialAccount.workspace_id == workspace.id, FinancialAccount.user_id == user_id)
        transactions = list((await self.session.scalars(select(FinancialTransaction).where(FinancialTransaction.id.in_(transaction_ids)).order_by(FinancialTransaction.created_at.desc()).limit(100))).all())
        record_audit(self.session, actor_id=actor_id or user_id, action=AuditAction.READ, entity=AuditEntity.ACCOUNT, entity_id=user_id, event_type="PORTFOLIO_INSPECTION", event_metadata={"workspace_id": str(workspace.id)})
        await self.session.commit()
        return PortfolioSummary(portfolio.id, workspace.id, user_id, balances, positions, transactions)
