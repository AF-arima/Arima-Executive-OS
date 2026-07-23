from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_role
from app.auth.service import AuthenticationService
from app.database.models import User
from app.database.session import get_session
from app.schemas.auth import CurrentUserResponse, RoleAssignmentRequest

router = APIRouter(prefix="/admin", tags=["administration"])
SessionDependency = Annotated[AsyncSession, Depends(get_session)]
Administrator = Annotated[User, Depends(require_role("administrator"))]


@router.post(
    "/users/{user_id}/roles",
    response_model=CurrentUserResponse,
)
async def assign_role(
    user_id: UUID,
    data: RoleAssignmentRequest,
    session: SessionDependency,
    administrator: Administrator,
) -> User:
    return await AuthenticationService(session).assign_role(
        user_id,
        data.role_name,
    )


@router.delete(
    "/users/{user_id}/roles/{role_name}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_role(
    user_id: UUID,
    role_name: str,
    session: SessionDependency,
    administrator: Administrator,
) -> Response:
    await AuthenticationService(session).remove_role(
        user_id,
        role_name.strip().lower(),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
