from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_validator


ETH_WALLET_PATTERN = r"^0x[a-fA-F0-9]{40}$"


class WithdrawalIntakeRequest(BaseModel):
    full_name: str = Field(min_length=1, max_length=200)
    amount_eth: Decimal = Field(gt=Decimal("0"), max_digits=30, decimal_places=18)
    wallet_address: str = Field(pattern=ETH_WALLET_PATTERN)
    network: Literal["Ethereum Mainnet"] = "Ethereum Mainnet"
    note: str | None = Field(default=None, max_length=2_000)

    @field_validator("full_name", "wallet_address", mode="before")
    @classmethod
    def strip_required_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("note", mode="before")
    @classmethod
    def normalize_note(cls, value: object) -> object:
        if value is None:
            return None
        if not isinstance(value, str):
            return value
        normalized = value.strip()
        return normalized or None


class WithdrawalIntakeResponse(BaseModel):
    message: str
