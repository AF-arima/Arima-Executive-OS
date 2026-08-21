from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_active_user, require_founder_control
from app.database.models import User
from app.database.session import get_session
from app.schemas.portfolio import BalanceResponse, LedgerActivityResponse, PortfolioResponse, PositionResponse
from app.services.portfolio import PortfolioService

router = APIRouter(prefix="/portfolio", tags=["portfolio"])
SessionDependency = Annotated[AsyncSession, Depends(get_session)]
CurrentUser = Annotated[User, Depends(get_current_active_user)]
FounderUser = Annotated[User, Depends(require_founder_control)]


async def _response(user_id: UUID, session, workspace_id: UUID | None = None, actor_id: UUID | None = None) -> PortfolioResponse:
    try:
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
