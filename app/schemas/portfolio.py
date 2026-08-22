from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class BalanceResponse(BaseModel):
    asset: str
    authoritative_balance: Decimal
    available_balance: Decimal
    reserved_balance: Decimal
    pending_balance: Decimal


class PositionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    asset: str
    quantity: Decimal
    average_cost: Decimal | None
    realized_pnl: Decimal
    unrealized_pnl: Decimal


class LedgerActivityResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    transaction_type: str
    status: str
    reference: str | None
    source: str
    created_at: datetime
    posted_at: datetime | None


class PortfolioResponse(BaseModel):
    portfolio_id: UUID
    workspace_id: UUID
    user_id: UUID
    balances: list[BalanceResponse]
    positions: list[PositionResponse]
    recent_ledger_activity: list[LedgerActivityResponse]
