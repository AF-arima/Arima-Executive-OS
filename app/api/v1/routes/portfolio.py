from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_active_user, require_founder_control
from app.auth.csrf import require_valid_csrf
from app.database.models import SettledTrade, User
from app.database.session import get_session
from app.schemas.portfolio import BalanceResponse, LedgerActivityResponse, PortfolioResponse, PositionResponse
from app.schemas.trades import TradeCreate, TradeRead, TradeReverse
from app.services.portfolio import PortfolioService
from app.services.trade_accounting import TradeAccountingError, TradeAuthorizationError, TradeConflictError, TradeAccountingService

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
