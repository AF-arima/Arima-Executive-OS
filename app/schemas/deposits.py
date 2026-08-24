from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class DepositCreate(BaseModel):
    asset: str = Field(min_length=1, max_length=32)
    amount: Decimal = Field(gt=0, max_digits=38, decimal_places=18)
    reference: str = Field(min_length=1, max_length=160)
    reason: str = Field(min_length=1, max_length=1000)
    idempotency_key: str = Field(min_length=16, max_length=180)


class DepositRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    target_user_id: UUID
    founder_actor_id: UUID
    asset: str
    amount: Decimal
    reference: str
    reason: str
    idempotency_key: str
    financial_transaction_id: UUID
    financial_account_id: UUID
