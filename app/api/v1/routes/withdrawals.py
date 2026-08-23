from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from app.auth.csrf import require_valid_csrf
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_active_user, require_founder_control
from app.auth.exceptions import EmailDeliveryError
from app.database.models import User, WithdrawalCircuitState, WithdrawalRequest, WithdrawalState, Workspace
from app.database.session import get_session
from app.email.factory import get_transactional_email_service
from app.schemas.operations import CircuitBreakerRequest, CircuitBreakerResponse, WithdrawalRequestCreate, WithdrawalResponse, WithdrawalTransitionRequest
from app.services.operations import (
    BalanceUnavailableError, InsufficientBalanceError,
    InvalidWithdrawalTransition, OperationsError, WithdrawalCircuitOpenError,
    WithdrawalService, mask_wallet,
)
from app.services.risk_contract import CircuitStateUnavailableError
from app.services.identity import FinancialContextError, FinancialContextResolver

router = APIRouter(prefix="/withdrawals", tags=["withdrawals"])
SessionDependency = Annotated[AsyncSession, Depends(get_session)]
CurrentUser = Annotated[User, Depends(get_current_active_user)]
FounderUser = Annotated[User, Depends(require_founder_control)]


def _csrf_guard(request: Request) -> None:
    require_valid_csrf(request)


def _response(item: WithdrawalRequest) -> WithdrawalResponse:
    return WithdrawalResponse(
        id=item.id, workspace_id=item.workspace_id, user_id=item.user_id,
        amount=item.amount, currency=item.currency, network=item.network,
        masked_wallet=mask_wallet(item.destination_wallet_address), state=WithdrawalState(item.state).value,
        state_reason=item.state_reason, notification_status=item.notification_status,
        created_at=item.created_at, updated_at=item.updated_at,
        reviewed_by_id=item.reviewed_by_id, reviewed_at=item.reviewed_at,
        approved_by_id=item.approved_by_id, approved_at=item.approved_at,
    )


def _email_service():
    try:
        return get_transactional_email_service()
    except EmailDeliveryError:
        return None


@router.post("", response_model=WithdrawalResponse, status_code=status.HTTP_201_CREATED)
async def create_withdrawal(data: WithdrawalRequestCreate, actor: CurrentUser, session: SessionDependency) -> WithdrawalResponse:
    try:
        item = await WithdrawalService(session, email_service=_email_service()).create(data, actor, require_authoritative_context=True)
    except (BalanceUnavailableError, InsufficientBalanceError, WithdrawalCircuitOpenError, CircuitStateUnavailableError, OperationsError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return _response(item)


@router.get("/operations/list", response_model=list[WithdrawalResponse])
async def list_withdrawals(
    actor: FounderUser, session: SessionDependency,
    workspace_id: UUID = Query(...),
    state: str | None = Query(default=None),
    customer_email: str | None = Query(default=None),
) -> list[WithdrawalResponse]:
    statement = select(WithdrawalRequest).order_by(WithdrawalRequest.created_at.desc()).limit(100)
    workspace = await session.get(Workspace, workspace_id)
    if workspace is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    try:
        await FinancialContextResolver(session).resolve(actor=actor, workspace_id=workspace_id, account_id=workspace.owner_id)
    except FinancialContextError as error:
        raise HTTPException(status_code=403, detail="Workspace access denied") from error
    statement = statement.where(WithdrawalRequest.workspace_id == workspace_id)
    if state is not None:
        try:
            statement = statement.where(WithdrawalRequest.state == WithdrawalState(state))
        except ValueError as error:
            raise HTTPException(status_code=422, detail="Invalid withdrawal state") from error
    if customer_email:
        statement = statement.join(User, User.id == WithdrawalRequest.user_id).where(User.email == customer_email.strip().lower())
    return [_response(item) for item in (await session.scalars(statement)).all()]


@router.get("/{request_id}", response_model=WithdrawalResponse)
async def get_withdrawal(request_id: UUID, actor: CurrentUser, session: SessionDependency) -> WithdrawalResponse:
    item = await session.get(WithdrawalRequest, request_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Withdrawal request not found")
    try:
        await WithdrawalService(session).authorize_request_access(item, actor)
    except OperationsError as error:
        raise HTTPException(status_code=404, detail="Withdrawal request not found") from error
    return _response(item)


@router.post("/{request_id}/cancel", response_model=WithdrawalResponse)
async def cancel_withdrawal(request_id: UUID, data: WithdrawalTransitionRequest, actor: CurrentUser, session: SessionDependency) -> WithdrawalResponse:
    item = await session.get(WithdrawalRequest, request_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Withdrawal request not found")
    try:
        service = WithdrawalService(session)
        await service.authorize_request_access(item, actor)
        return _response(await service.transition(request_id, WithdrawalState.CANCELLED, actor, reason=data.reason, require_authoritative_context=True))
    except (InvalidWithdrawalTransition, OperationsError) as error:
        if isinstance(error, OperationsError) and "authorized" in str(error).lower():
            raise HTTPException(status_code=404, detail="Withdrawal request not found") from error
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/operations/circuit/{workspace_id}", response_model=CircuitBreakerResponse)
async def set_circuit(workspace_id: UUID, data: CircuitBreakerRequest, actor: FounderUser, session: SessionDependency, _csrf: Annotated[None, Depends(_csrf_guard)]) -> CircuitBreakerResponse:
    row = await WithdrawalService(session).change_circuit(workspace_id, WithdrawalCircuitState(data.state), data.reason, actor, require_authoritative_context=True)
    return CircuitBreakerResponse(workspace_id=row.workspace_id, state=WithdrawalCircuitState(row.state).value, reason=row.reason, changed_by_id=row.changed_by_id, changed_at=row.changed_at)


@router.post("/operations/{request_id}/{target}", response_model=WithdrawalResponse)
async def transition_withdrawal(request_id: UUID, target: str, data: WithdrawalTransitionRequest, actor: FounderUser, session: SessionDependency) -> WithdrawalResponse:
    if target not in {"under_review", "approved", "rejected", "cancelled", "blocked"}:
        raise HTTPException(status_code=403, detail="Execution transitions are not enabled")
    try:
        target_state = WithdrawalState(target)
        item = await WithdrawalService(session).transition(request_id, target_state, actor, reason=data.reason, require_authoritative_context=True)
    except ValueError as error:
        raise HTTPException(status_code=422, detail="Invalid withdrawal state") from error
    except (InvalidWithdrawalTransition, OperationsError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return _response(item)


@router.get("/operations/circuit/{workspace_id}", response_model=CircuitBreakerResponse)
async def get_circuit(workspace_id: UUID, actor: FounderUser, session: SessionDependency) -> CircuitBreakerResponse:
    try:
        workspace = await session.get(Workspace, workspace_id)
        if workspace is None:
            raise OperationsError("Circuit workspace is unavailable")
        await FinancialContextResolver(session).resolve(actor=actor, workspace_id=workspace_id, account_id=workspace.owner_id)
        row = await WithdrawalService(session)._circuit(workspace_id)
    except (CircuitStateUnavailableError, OperationsError, FinancialContextError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    await session.commit()
    return CircuitBreakerResponse(workspace_id=row.workspace_id, state=WithdrawalCircuitState(row.state).value, reason=row.reason, changed_by_id=row.changed_by_id, changed_at=row.changed_at)
