from typing import Any

from fastapi import HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from app.schemas.auth import UserLogin


async def login_credentials(request: Request) -> UserLogin:
    content_type = request.headers.get("content-type", "").lower()

    if content_type.startswith(
        ("application/x-www-form-urlencoded", "multipart/form-data")
    ):
        form = await request.form()
        payload: Any = {
            "email": form.get("username"),
            "password": form.get("password"),
        }
    else:
        try:
            payload = await request.json()
        except ValueError as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Invalid login payload",
            ) from error

    try:
        return UserLogin.model_validate(payload)
    except ValidationError as error:
        raise RequestValidationError(error.errors()) from error
