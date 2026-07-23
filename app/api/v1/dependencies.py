from typing import Annotated, Any

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_any_role
from app.database.models import User
from app.database.session import get_session

SessionDependency = Annotated[AsyncSession, Depends(get_session)]
AnalyticsUser = Annotated[
    User,
    Depends(
        require_any_role(
            "administrator",
            "executive",
            "manager",
            "analyst",
            "viewer",
        )
    ),
]

AUTHENTICATED_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"description": "Missing or invalid access token"},
    403: {"description": "Insufficient permissions"},
    422: {"description": "Request validation failed"},
}
