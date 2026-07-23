from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_active_user
from app.auth.login import login_credentials
from app.auth.service import AuthenticationService
from app.database.models import User
from app.database.session import get_session
from app.schemas.auth import (
    CurrentUserResponse,
    RefreshTokenRequest,
    TokenResponse,
    UserLogin,
    UserPublicResponse,
    UserRegistration,
)

router = APIRouter(prefix="/auth", tags=["authentication"])
SessionDependency = Annotated[AsyncSession, Depends(get_session)]
LOGIN_OPENAPI = {
    "requestBody": {
        "required": True,
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["email", "password"],
                    "properties": {
                        "email": {"type": "string", "format": "email"},
                        "password": {
                            "type": "string",
                            "format": "password",
                            "writeOnly": True,
                        },
                    },
                }
            },
            "application/x-www-form-urlencoded": {
                "schema": {
                    "type": "object",
                    "required": ["username", "password"],
                    "properties": {
                        "username": {
                            "type": "string",
                            "format": "email",
                        },
                        "password": {
                            "type": "string",
                            "format": "password",
                            "writeOnly": True,
                        },
                    },
                }
            },
        },
    }
}


@router.post(
    "/register",
    response_model=UserPublicResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register(
    data: UserRegistration,
    session: SessionDependency,
) -> User:
    return await AuthenticationService(session).register_user(data)


@router.post(
    "/login",
    response_model=TokenResponse,
    openapi_extra=LOGIN_OPENAPI,
)
async def login(
    data: Annotated[UserLogin, Depends(login_credentials)],
    session: SessionDependency,
    response: Response,
) -> TokenResponse:
    pair = await AuthenticationService(session).login(data)
    _set_no_store(response)
    return TokenResponse(
        access_token=pair.access_token,
        refresh_token=pair.refresh_token,
        expires_in=pair.expires_in,
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    data: RefreshTokenRequest,
    session: SessionDependency,
    response: Response,
) -> TokenResponse:
    pair = await AuthenticationService(session).refresh_token_pair(
        data.refresh_token.get_secret_value()
    )
    _set_no_store(response)
    return TokenResponse(
        access_token=pair.access_token,
        refresh_token=pair.refresh_token,
        expires_in=pair.expires_in,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    data: RefreshTokenRequest,
    session: SessionDependency,
) -> Response:
    await AuthenticationService(session).logout(
        data.refresh_token.get_secret_value()
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=CurrentUserResponse)
async def me(
    current_user: Annotated[
        User,
        Depends(get_current_active_user),
    ],
    response: Response,
) -> User:
    _set_no_store(response)
    return current_user


def _set_no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
