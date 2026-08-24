from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_active_user, require_founder_control
from app.auth.csrf import require_valid_csrf
from app.database.models import (
    FinancialAccount,
    FinancialTransaction,
    LedgerEntry,
    Portfolio,
    PortfolioPosition,
    SettledTrade,
    User,
    Workspace,
    WorkspaceMembership,
)
from app.database.session import get_session
from app.schemas.portfolio import (
    BalanceResponse,
    FinancialAccountStateResponse,
    FinancialStateResponse,
    LedgerActivityResponse,
    PortfolioResponse,
    PositionResponse,
)
from app.schemas.deposits import DepositCreate, DepositRead
from app.schemas.trades import TradeCreate, TradeRead, TradeReverse
from app.services.portfolio import PortfolioService
from app.services.identity import FinancialContextError, FinancialContextResolver
from app.services.ledger import LedgerService
from app.services.trade_accounting import TradeAccountingError, TradeAuthorizationError, TradeConflictError, TradeAccountingService
from app.services.deposit_accounting import DepositAccountingError, DepositAuthorizationError, DepositConflictError, DepositAccountingService

router = APIRouter(prefix="/portfolio", tags=["portfolio"])
SessionDependency = Annotated[AsyncSession, Depends(get_session)]
CurrentUser = Annotated[User, Depends(get_current_active_user)]
FounderUser = Annotated[User, Depends(require_founder_control)]


def _trade_csrf(request: Request) -> None:
    require_valid_csrf(request)


async def _response(user_id: UUID, session, workspace_id: UUID | None = None, actor_id: UUID | None = None) -> PortfolioResponse:
    try:
        if actor_id is None:
            raise HTTPException(status_code=401, detail="Authenticated actor is required")
        summary = await PortfolioService(session).summary(user_id=user_id, workspace_id=workspace_id, actor_id=actor_id, require_authoritative_context=True)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return PortfolioResponse(
        portfolio_id=summary.portfolio_id, workspace_id=summary.workspace_id, user_id=summary.user_id,
        balances=[BalanceResponse(asset=asset, authoritative_balance=s.authoritative_balance, available_balance=s.available_balance, reserved_balance=s.reserved_balance, pending_balance=s.pending_balance) for asset, s in summary.balances],
        positions=[PositionResponse.model_validate(position) for position in summary.positions],
        recent_ledger_activity=[LedgerActivityResponse.model_validate(item) for item in summary.transactions],
    )


@router.get("", response_model=PortfolioResponse)
async def portfolio_summary(actor: CurrentUser, session: SessionDependency) -> PortfolioResponse:
    return await _response(actor.id, session, actor_id=actor.id)


@router.get("/operations/customers/{user_id}", response_model=PortfolioResponse)
async def customer_portfolio(user_id: UUID, actor: FounderUser, session: SessionDependency) -> PortfolioResponse:
    return await _response(user_id, session, actor_id=actor.id)


@router.get(
    "/operations/customers/{user_id}/financial-state",
    response_model=FinancialStateResponse,
)
async def customer_financial_state(
    user_id: UUID,
    actor: FounderUser,
    session: SessionDependency,
) -> FinancialStateResponse:
    target = await session.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Customer account not found")

    workspaces = list((await session.scalars(
        select(Workspace)
        .outerjoin(WorkspaceMembership, WorkspaceMembership.workspace_id == Workspace.id)
        .where(
            Workspace.owner_id == user_id,
            (Workspace.owner_id == user_id) | (WorkspaceMembership.user_id == user_id),
        )
        .order_by(Workspace.created_at)
    )).all())
    if len(workspaces) != 1:
        raise HTTPException(status_code=409, detail="Customer workspace scope is unavailable")
    workspace = workspaces[0]
    try:
        await FinancialContextResolver(session).resolve(
            actor=actor,
            workspace_id=workspace.id,
            account_id=target.id,
        )
    except FinancialContextError as error:
        raise HTTPException(status_code=404, detail="Customer financial scope is unavailable") from error

    accounts = list((await session.scalars(
        select(FinancialAccount)
        .where(
            FinancialAccount.workspace_id == workspace.id,
            FinancialAccount.user_id == target.id,
        )
        .order_by(FinancialAccount.asset)
    )).all())
    ledger = LedgerService(session)
    account_states = [
        FinancialAccountStateResponse(
            id=account.id,
            asset=account.asset,
            account_kind=account.account_kind,
            status=account.status,
            balance=BalanceResponse(
                asset=account.asset,
                authoritative_balance=snapshot.authoritative_balance,
                available_balance=snapshot.available_balance,
                reserved_balance=snapshot.reserved_balance,
                pending_balance=snapshot.pending_balance,
            ),
        )
        for account in accounts
        for snapshot in [await ledger.balance(account_id=account.id, asset=account.asset)]
    ]

    portfolio = await session.scalar(
        select(Portfolio).where(
            Portfolio.workspace_id == workspace.id,
            Portfolio.user_id == target.id,
        )
    )
    positions: list[PositionResponse] = []
    if portfolio is not None:
        rows = list((await session.scalars(
            select(PortfolioPosition)
            .where(PortfolioPosition.portfolio_id == portfolio.id)
            .order_by(PortfolioPosition.asset)
        )).all())
        positions = [PositionResponse.model_validate(row) for row in rows]

    transaction_ids = select(LedgerEntry.transaction_id).join(
        FinancialAccount,
        FinancialAccount.id == LedgerEntry.financial_account_id,
    ).where(
        FinancialAccount.workspace_id == workspace.id,
        FinancialAccount.user_id == target.id,
    )
    ledger_activity_count = int(await session.scalar(
        select(func.count(func.distinct(FinancialTransaction.id))).where(
            FinancialTransaction.id.in_(transaction_ids),
        )
    ) or 0)
    settled_trade_count = int(await session.scalar(
        select(func.count(SettledTrade.id)).where(
            SettledTrade.workspace_id == workspace.id,
            SettledTrade.target_user_id == target.id,
        )
    ) or 0)
    return FinancialStateResponse(
        user_id=target.id,
        workspace_id=workspace.id,
        portfolio_id=portfolio.id if portfolio is not None else None,
        financial_accounts=account_states,
        positions=positions,
        ledger_activity_count=ledger_activity_count,
        settled_trade_count=settled_trade_count,
    )


@router.post("/operations/customers/{user_id}/trades", response_model=TradeRead, status_code=201)
async def record_customer_trade(user_id: UUID, data: TradeCreate, actor: FounderUser, session: SessionDependency, _csrf: Annotated[None, Depends(_trade_csrf)]) -> TradeRead:
    try:
        return TradeRead.model_validate(await TradeAccountingService(session).record(actor=actor, target_user_id=user_id, data=data))
    except TradeAuthorizationError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (TradeConflictError, TradeAccountingError, ValueError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get("/operations/customers/{user_id}/trades", response_model=list[TradeRead])
async def list_customer_trades(user_id: UUID, actor: FounderUser, session: SessionDependency) -> list[TradeRead]:
    try:
        service = TradeAccountingService(session)
        _, workspace = await service._target(actor=actor, target_user_id=user_id)
        rows = list((await session.scalars(select(SettledTrade).where(SettledTrade.workspace_id == workspace.id, SettledTrade.target_user_id == user_id).order_by(SettledTrade.created_at.desc()))).all())
        return [TradeRead.model_validate(row) for row in rows]
    except TradeAuthorizationError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.post("/operations/customers/{user_id}/trades/{trade_id}/reverse", response_model=TradeRead)
async def reverse_customer_trade(user_id: UUID, trade_id: UUID, data: TradeReverse, actor: FounderUser, session: SessionDependency, _csrf: Annotated[None, Depends(_trade_csrf)]) -> TradeRead:
    try:
        return TradeRead.model_validate(await TradeAccountingService(session).reverse(actor=actor, target_user_id=user_id, trade_id=trade_id, idempotency_key=data.idempotency_key, reason=data.reason))
    except TradeAuthorizationError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (TradeConflictError, TradeAccountingError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/operations/customers/{user_id}/deposits", response_model=DepositRead, status_code=201)
async def record_customer_deposit(
    user_id: UUID,
    data: DepositCreate,
    actor: FounderUser,
    session: SessionDependency,
    _csrf: Annotated[None, Depends(_trade_csrf)],
) -> DepositRead:
    try:
        result = await DepositAccountingService(session).record(actor=actor, target_user_id=user_id, data=data)
        return DepositRead(
            id=result.id,
            workspace_id=result.workspace_id,
            target_user_id=result.target_user_id,
            founder_actor_id=result.founder_actor_id,
            asset=result.asset,
            amount=result.amount,
            reference=result.reference,
            reason=result.reason,
            idempotency_key=result.idempotency_key,
            financial_transaction_id=result.financial_transaction_id,
            financial_account_id=result.financial_account_id,
        )
    except DepositAuthorizationError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (DepositConflictError, DepositAccountingError, ValueError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
