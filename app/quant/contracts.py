from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol
from uuid import UUID


class MarketRegime(StrEnum):
    TRENDING = "trending"
    RANGE_BOUND = "range_bound"
    VOLATILE = "volatile"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ResearchProvenance:
    provider: str
    source: str
    instrument: str
    observed_at: datetime
    received_at: datetime
    reference: str

    def __post_init__(self) -> None:
        if not all((self.provider, self.source, self.instrument, self.reference)):
            raise ValueError("Research provenance is incomplete")
        for value in (self.observed_at, self.received_at):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("Research provenance timestamps must be timezone-aware")


@dataclass(frozen=True, slots=True)
class ResearchSignal:
    signal_id: UUID
    tenant_id: UUID | None
    workspace_id: UUID
    actor_id: UUID | None
    account_id: UUID
    asset: str
    strategy: str
    timeframe: str
    regime: MarketRegime
    direction: str
    confidence: Decimal
    entry: Decimal
    stop: Decimal
    targets: tuple[Decimal, ...]
    expected_risk_reward: Decimal
    rationale: str
    timestamp: datetime
    provenance: ResearchProvenance

    def __post_init__(self) -> None:
        if not self.asset or not self.strategy or not self.timeframe or not self.rationale:
            raise ValueError("Research signal requires asset, strategy, and rationale")
        if self.direction not in {"long", "short"}:
            raise ValueError("Research signal direction is invalid")
        if not 0 <= self.confidence <= 1:
            raise ValueError("Research confidence must be between zero and one")
        if self.entry <= 0 or self.stop <= 0 or not self.targets:
            raise ValueError("Research prices must be positive and include a target")
        if self.expected_risk_reward <= 0:
            raise ValueError("Expected risk/reward must be positive")
        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() is None:
            raise ValueError("Research timestamp must be timezone-aware")
        if self.timestamp != self.provenance.received_at:
            raise ValueError("Signal timestamp must match received provenance")


class QLabResearchProvider(Protocol):
    async def research(self, *, tenant_id: UUID, workspace_id: UUID, actor_id: UUID, account_id: UUID, user: object, run_id: UUID, asset: str, strategy: str) -> ResearchSignal: ...


class QLabUnavailableError(RuntimeError):
    def __init__(self, message: str, *, state: str | None = None) -> None:
        super().__init__(message)
        self.state = state


class QLabService:
    """Read-only adapter that accepts only provider-produced normalized signals."""

    def __init__(self, provider: QLabResearchProvider | None = None) -> None:
        self.provider = provider

    async def research(self, *, tenant_id: UUID, workspace_id: UUID, actor_id: UUID, account_id: UUID, user: object, run_id: UUID, asset: str, strategy: str) -> ResearchSignal:
        if self.provider is None:
            raise QLabUnavailableError("No verified QLab research provider is configured")
        signal = await self.provider.research(
            tenant_id=tenant_id, workspace_id=workspace_id, actor_id=actor_id,
            account_id=account_id, user=user, run_id=run_id, asset=asset, strategy=strategy,
        )
        if signal.workspace_id != workspace_id or signal.account_id != account_id:
            raise QLabUnavailableError("Research signal identity does not match the requested account")
        if signal.tenant_id != tenant_id or signal.actor_id != actor_id:
            raise QLabUnavailableError("Research signal identity does not match the requested execution context")
        return signal
