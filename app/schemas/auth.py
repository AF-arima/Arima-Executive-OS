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
    remember_me: bool = False
    otp: SecretStr | None = None

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
    token_type: str = "bearer"
    expires_in: int
    csrf_token: str


class RoleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    name: str
    description: str | None


class WorkspaceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    name: str
    owner_id: UUID


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
    workspace: WorkspaceResponse | None = Field(
        default=None,
        validation_alias="owned_workspace",
        serialization_alias="workspace",
    )


class RegistrationResponse(StrictSchema):
    user: UserPublicResponse
    verification_required: bool = True


class AuthSessionResponse(TokenResponse):
    user: CurrentUserResponse


class CsrfTokenResponse(StrictSchema):
    csrf_token: str


class EmailAddressRequest(StrictSchema):
    email: EmailStr

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: EmailStr) -> str:
        return str(value).lower()


class SecurityTokenRequest(StrictSchema):
    token: SecretStr

    @field_validator("token")
    @classmethod
    def validate_token_size(cls, value: SecretStr) -> SecretStr:
        if not 20 <= len(value.get_secret_value()) <= 4_096:
            raise ValueError("Invalid security token")
        return value


class PasswordResetRequest(SecurityTokenRequest):
    password: SecretStr

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: SecretStr) -> SecretStr:
        return _validate_password_strength(value)


class ChangePasswordRequest(StrictSchema):
    current_password: SecretStr
    password: SecretStr

    @field_validator("current_password")
    @classmethod
    def validate_current_password_size(cls, value: SecretStr) -> SecretStr:
        if not 1 <= len(value.get_secret_value()) <= 128:
            raise ValueError("Invalid password length")
        return value

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: SecretStr) -> SecretStr:
        return _validate_password_strength(value)


class MFACodeRequest(StrictSchema):
    code: str = Field(min_length=6, max_length=6, pattern=r"^[0-9]{6}$")


class MFARecoveryRequest(StrictSchema):
    reason: str = Field(min_length=8, max_length=500)

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) < 8:
            raise ValueError("A recovery reason is required")
        return normalized


class MFAEnrollmentResponse(StrictSchema):
    enabled: bool
    otpauth_uri: str


class ChangeEmailRequest(StrictSchema):
    new_email: EmailStr
    current_password: SecretStr

    @field_validator("new_email")
    @classmethod
    def normalize_email(cls, value: EmailStr) -> str:
        return str(value).lower()

    @field_validator("current_password")
    @classmethod
    def validate_current_password_size(cls, value: SecretStr) -> SecretStr:
        if not 1 <= len(value.get_secret_value()) <= 128:
            raise ValueError("Invalid password length")
        return value


class UserProfileUpdate(StrictSchema):
    first_name: str | None = Field(default=None, min_length=1, max_length=100)
    last_name: str | None = Field(default=None, min_length=1, max_length=100)

    @field_validator("first_name", "last_name")
    @classmethod
    def normalize_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("Name cannot be blank")
        return normalized


class SessionResponse(StrictSchema):
    family_id: UUID
    created_at: datetime
    last_used_at: datetime | None
    expires_at: datetime
    is_persistent: bool
    user_agent: str | None
    ip_address: str | None
    current: bool = False


class SessionListResponse(StrictSchema):
    items: list[SessionResponse]


def _validate_password_strength(value: SecretStr) -> SecretStr:
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


class RoleAssignmentRequest(StrictSchema):
    role_name: str = Field(min_length=1, max_length=100)

    @field_validator("role_name")
    @classmethod
    def normalize_role_name(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("Role name cannot be blank")
        return normalized
