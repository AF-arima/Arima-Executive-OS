from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import EmailStr, Field, field_validator, model_validator

from app.schemas.auth import StrictSchema
from app.schemas.portfolio import BalanceResponse


class CustomerSupportSummary(StrictSchema):
    id: UUID
    name: str
    email: EmailStr
    is_active: bool
    is_verified: bool
    roles: list[str]
    created_at: datetime
    last_login_at: datetime | None
    last_login_ip: str | None
    workspace_id: UUID | None


class SecurityEventSummary(StrictSchema):
    event_type: str
    occurred_at: datetime
    ip_address: str | None
    user_agent: str | None


class SessionSummary(StrictSchema):
    family_id: UUID
    created_at: datetime
    last_used_at: datetime | None
    expires_at: datetime
    revoked_at: datetime | None
    revoked_reason: str | None
    is_current: bool


class CustomerSupportDetail(CustomerSupportSummary):
    password_changed_at: datetime | None
    security_events: list[SecurityEventSummary]
    sessions: list[SessionSummary]
    support_status: str
    issue_indicators: list[str]
    portfolio_balances: list[BalanceResponse]
    withdrawal_statuses: list[str]


class WithdrawalRequestCreate(StrictSchema):
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    amount: Decimal = Field(gt=0, max_digits=38, decimal_places=18)
    currency: str = Field(min_length=3, max_length=12)
    destination_wallet_address: str = Field(min_length=42, max_length=128)
    network: str = Field(min_length=1, max_length=64)
    confirmation: bool
    risk_acknowledgement: bool
    idempotency_key: str = Field(min_length=16, max_length=160)

    @field_validator("first_name", "last_name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Name cannot be blank")
        return value

    @field_validator("currency", "network")
    @classmethod
    def normalize_label(cls, value: str) -> str:
        return value.strip().upper().replace(" ", "_")

    @field_validator("destination_wallet_address")
    @classmethod
    def validate_ethereum_address(cls, value: str) -> str:
        normalized = value.strip()
        if not (normalized.startswith("0x") and len(normalized) == 42):
            raise ValueError("Invalid Ethereum wallet address")
        try:
            int(normalized[2:], 16)
        except ValueError as error:
            raise ValueError("Invalid Ethereum wallet address") from error
        return normalized

    @model_validator(mode="after")
    def validate_acknowledgements(self):
        if not self.confirmation or not self.risk_acknowledgement:
            raise ValueError("Withdrawal confirmation and risk acknowledgement are required")
        if self.network != "ETHEREUM_MAINNET" or self.currency != "ETH":
            raise ValueError("Only ETH withdrawals on Ethereum Mainnet are supported")
        return self


class WithdrawalResponse(StrictSchema):
    id: UUID
    workspace_id: UUID
    user_id: UUID
    amount: Decimal
    currency: str
    network: str
    masked_wallet: str
    state: str
    state_reason: str | None
    notification_status: str
    created_at: datetime
    updated_at: datetime
    reviewed_by_id: UUID | None
    reviewed_at: datetime | None
    approved_by_id: UUID | None
    approved_at: datetime | None


class WithdrawalTransitionRequest(StrictSchema):
    reason: str = Field(min_length=1, max_length=500)


class CircuitBreakerRequest(StrictSchema):
    state: str
    reason: str = Field(min_length=1, max_length=500)

    @field_validator("state")
    @classmethod
    def validate_state(cls, value: str) -> str:
        value = value.strip().lower()
        if value not in {"enabled", "paused", "emergency_stop"}:
            raise ValueError("Invalid withdrawal circuit-breaker state")
        return value


class CircuitBreakerResponse(StrictSchema):
    workspace_id: UUID
    state: str
    reason: str | None
    changed_by_id: UUID | None
    changed_at: datetime
