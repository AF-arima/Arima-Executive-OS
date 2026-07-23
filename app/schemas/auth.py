from datetime import datetime
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    SecretStr,
    field_validator,
)


class StrictSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class UserRegistration(StrictSchema):
    email: EmailStr
    password: SecretStr
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: EmailStr) -> str:
        return str(value).lower()

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: SecretStr) -> SecretStr:
        password = value.get_secret_value()
        if not 12 <= len(password) <= 128:
            raise ValueError("Password must be between 12 and 128 characters")
        requirements = (
            any(character.islower() for character in password),
            any(character.isupper() for character in password),
            any(character.isdigit() for character in password),
            any(not character.isalnum() for character in password),
        )
        if not all(requirements):
            raise ValueError(
                "Password must include upper and lower case letters, "
                "a number, and a special character"
            )
        return value

    @field_validator("first_name", "last_name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Name cannot be blank")
        return normalized


class UserLogin(StrictSchema):
    email: EmailStr
    password: SecretStr

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: EmailStr) -> str:
        return str(value).lower()

    @field_validator("password")
    @classmethod
    def validate_password_size(cls, value: SecretStr) -> SecretStr:
        if not 1 <= len(value.get_secret_value()) <= 128:
            raise ValueError("Invalid password length")
        return value


class TokenResponse(StrictSchema):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class RefreshTokenRequest(StrictSchema):
    refresh_token: SecretStr

    @field_validator("refresh_token")
    @classmethod
    def validate_token_size(cls, value: SecretStr) -> SecretStr:
        if not 1 <= len(value.get_secret_value()) <= 4096:
            raise ValueError("Invalid token length")
        return value


class RoleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    name: str
    description: str | None


class UserPublicResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    email: EmailStr
    first_name: str
    last_name: str
    is_active: bool
    is_verified: bool
    created_at: datetime
    updated_at: datetime


class CurrentUserResponse(UserPublicResponse):
    roles: list[RoleResponse]


class RoleAssignmentRequest(StrictSchema):
    role_name: str = Field(min_length=1, max_length=100)

    @field_validator("role_name")
    @classmethod
    def normalize_role_name(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("Role name cannot be blank")
        return normalized
