from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_founder_control
from app.database.models import User, Workspace, WithdrawalRequest
from app.database.session import get_session
from app.schemas.operations import CustomerSupportDetail, CustomerSupportSummary, SecurityEventSummary, SessionSummary
from app.services.operations import CustomerSupportService, OperationsError
from app.services.portfolio import PortfolioService

router = APIRouter(prefix="/support/customers", tags=["customer-support"])
FounderUser = Annotated[User, Depends(require_founder_control)]
SessionDependency = Annotated[AsyncSession, Depends(get_session)]


def _summary(user: User, workspace_id: UUID | None) -> CustomerSupportSummary:
    return CustomerSupportSummary(
        id=user.id, name=f"{user.first_name} {user.last_name}", email=user.email,
        is_active=user.is_active, is_verified=user.is_verified,
        roles=sorted(role.name for role in user.roles), created_at=user.created_at,
        last_login_at=user.last_login_at, last_login_ip=user.last_login_ip,
        workspace_id=workspace_id,
    )


@router.get("", response_model=list[CustomerSupportSummary])
async def search_customers(
    q: Annotated[str, Query(min_length=1, max_length=320)],
    actor: FounderUser,
    session: SessionDependency,
) -> list[CustomerSupportSummary]:
    users = await CustomerSupportService(session).search(q, actor_id=actor.id)
    result: list[CustomerSupportSummary] = []
    service = CustomerSupportService(session)
    for user in users:
        workspace = await service.session.scalar(select(Workspace).where(Workspace.owner_id == user.id))
        result.append(_summary(user, workspace.id if workspace else None))
    return result


@router.get("/{user_id}", response_model=CustomerSupportDetail)
async def customer_detail(
    user_id: UUID,
    actor: FounderUser,
    session: SessionDependency,
) -> CustomerSupportDetail:
    try:
        user, events, sessions, workspace_id = await CustomerSupportService(session).detail(user_id, actor_id=actor.id)
    except OperationsError as error:
        raise HTTPException(status_code=409, detail="Customer workspace scope is unavailable") from error
    indicators = []
    if not user.is_verified:
        indicators.append("email_not_verified")
    if not user.is_active:
        indicators.append("account_inactive")
    if user.locked_until is not None:
        indicators.append("account_locked")
    try:
        portfolio = await PortfolioService(session).summary(user_id=user_id, actor_id=actor.id, workspace_id=workspace_id, require_authoritative_context=True)
        portfolio_balances = [
            {"asset": asset, "authoritative_balance": snapshot.authoritative_balance, "available_balance": snapshot.available_balance, "reserved_balance": snapshot.reserved_balance, "pending_balance": snapshot.pending_balance}
            for asset, snapshot in portfolio.balances
        ]
    except LookupError:
        portfolio_balances = []
    withdrawal_statuses = list((await session.scalars(select(WithdrawalRequest.state).where(WithdrawalRequest.user_id == user_id, WithdrawalRequest.workspace_id == workspace_id).order_by(WithdrawalRequest.created_at.desc()).limit(50))).all())
    return CustomerSupportDetail(
        **_summary(user, workspace_id).model_dump(),
        password_changed_at=user.password_changed_at,
        security_events=[SecurityEventSummary(event_type=item.event_type, occurred_at=item.occurred_at, ip_address=item.ip_address, user_agent=item.user_agent) for item in events],
        sessions=[SessionSummary(family_id=item.family_id, created_at=item.created_at, last_used_at=item.last_used_at, expires_at=item.expires_at, revoked_at=item.revoked_at, revoked_reason=item.revoked_reason, is_current=False) for item in sessions],
        support_status="active" if user.is_active else "inactive",
        issue_indicators=indicators,
        portfolio_balances=portfolio_balances,
        withdrawal_statuses=withdrawal_statuses,
    )
