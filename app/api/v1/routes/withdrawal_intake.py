from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select

from app.api.v1.dependencies import AUTHENTICATED_RESPONSES, SessionDependency
from app.auth.dependencies import get_current_active_user
from app.auth.exceptions import EmailDeliveryError
from app.auth.security import SecurityRateLimiter
from app.core.config import get_settings
from app.database.models import AuditAction, AuditEntity, User, Workspace
from app.email.factory import get_transactional_email_service
from app.email.service import TransactionalEmailService
from app.schemas.withdrawal_intake import (
    WithdrawalIntakeRequest,
    WithdrawalIntakeResponse,
)
from app.services.audit import record_audit


router = APIRouter(
    prefix="/requests",
    tags=["requests"],
    responses=AUTHENTICATED_RESPONSES,
)
CurrentUser = Annotated[User, Depends(get_current_active_user)]
EmailServiceDependency = Annotated[
    TransactionalEmailService,
    Depends(get_transactional_email_service),
]


@router.post(
    "/withdrawal-intake",
    response_model=WithdrawalIntakeResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def submit_withdrawal_intake(
    data: WithdrawalIntakeRequest,
    actor: CurrentUser,
    session: SessionDependency,
    request: Request,
    email_service: EmailServiceDependency,
) -> WithdrawalIntakeResponse:
    settings = get_settings()
    await SecurityRateLimiter(session).enforce(
        scope="withdrawal_intake",
        key=str(actor.id),
        limit=settings.withdrawal_intake_rate_limit_per_minute,
        window=timedelta(minutes=1),
    )

    submitted_at = datetime.now(UTC)
    workspace_id = await session.scalar(
        select(Workspace.id).where(Workspace.owner_id == actor.id)
    )
    workspace_reference = str(workspace_id) if workspace_id else "unassigned"
    try:
        await email_service.send_withdrawal_intake(
            recipient=str(settings.email_from_address) if settings.email_from_address else None,
            full_name=data.full_name,
            amount_eth=data.amount_eth,
            wallet_address=data.wallet_address,
            network=data.network,
            note=data.note,
            account_email=actor.email,
            workspace_reference=workspace_reference,
            submitted_at=submitted_at,
        )
    except EmailDeliveryError as error:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="We could not send your request right now. Please try again later.",
        ) from error

    correlation_id = getattr(request.state, "correlation_id", None)
    record_audit(
        session,
        actor_id=actor.id,
        action=AuditAction.CREATE,
        entity=AuditEntity.ACCOUNT,
        entity_id=actor.id,
        event_type="withdrawal_request_intake_submitted",
        event_metadata={
            "full_name": data.full_name,
            "amount_eth": str(data.amount_eth),
            "wallet_address": data.wallet_address,
            "network": data.network,
            "note": data.note,
            "account_email": actor.email,
            "workspace_id": workspace_reference,
            "submitted_at": submitted_at.isoformat(),
            "request_id": correlation_id,
        },
    )
    await session.commit()
    return WithdrawalIntakeResponse(
        message="Your request has been received. Our team will contact you within 48 hours."
    )
