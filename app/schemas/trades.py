from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TradeCreate(BaseModel):
    side: str
    base_asset: str
    quote_asset: str
    quantity: Decimal = Field(gt=0, max_digits=38, decimal_places=18)
    price: Decimal = Field(gt=0, max_digits=38, decimal_places=18)
    fee_amount: Decimal = Field(ge=0, max_digits=38, decimal_places=18)
    executed_at: datetime
    reason: str = Field(min_length=1, max_length=1000)
    idempotency_key: str = Field(min_length=16, max_length=180)
    external_execution_id: str | None = Field(default=None, max_length=180)

    @field_validator("side")
    @classmethod
    def validate_side(cls, value: str) -> str:
        value = value.strip().lower()
        if value not in {"buy", "sell"}:
            raise ValueError("Trade side must be buy or sell")
        return value


class TradeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    workspace_id: UUID
    target_user_id: UUID
    founder_actor_id: UUID
    reversal_of_id: UUID | None
    side: str
    base_asset: str
    quote_asset: str
    quantity: Decimal
    price: Decimal
    quote_value: Decimal
    fee_asset: str
    fee_amount: Decimal
    executed_at: datetime
    status: str
    external_execution_id: str | None
    idempotency_key: str
    reason: str
    created_at: datetime


class TradeReverse(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)
    idempotency_key: str = Field(min_length=16, max_length=180)
