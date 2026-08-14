from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import User
from app.database.repositories import WorkspaceMembershipRepository
from app.market.config import (
    CanonicalInstrument,
    MarketDataConfiguration,
    MarketDataProviderName,
    MarketDataSource,
)
from app.market.provider import (
    FreshnessState,
    MarketDataProvenance,
    ProviderVerification,
    VerificationState,
)


class MarketDataAccessError(PermissionError):
    pass


class MarketDataUnavailableError(RuntimeError):
    pass


class MarketDataConsumer(StrEnum):
    INTERNAL_SERVICE = "internal_service"
    Q_LAB = "q_lab"
    Q_RESEARCH = "q_research"
    LEADERSHIP = "leadership"


class MarketRecordType(StrEnum):
    MARKET_STATUS = "market_status"


class MarketSnapshotStatus(StrEnum):
    VERIFIED_INTERNAL = "verified_internal"
    VERIFIED_CUSTOMER_DISPLAY = "verified_customer_display"


class MarketSnapshot(BaseModel):
    """Canonical non-price snapshot passed only to approved server consumers."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    symbol: CanonicalInstrument
    provider: MarketDataProviderName
    source: MarketDataSource
    record_type: MarketRecordType
    as_of: datetime
    fetched_at: datetime
    status: MarketSnapshotStatus
    provenance: MarketDataProvenance
    freshness: FreshnessState

    @model_validator(mode="after")
    def validate_snapshot_contract(self) -> "MarketSnapshot":
        if self.freshness is not FreshnessState.FRESH:
            raise ValueError("Market snapshots must be fresh")
        if self.as_of != self.provenance.provider_timestamp:
            raise ValueError("Snapshot time must match provider provenance")
        if self.fetched_at != self.provenance.received_at:
            raise ValueError("Fetch time must match provider provenance")
        if self.symbol is not self.provenance.canonical:
            raise ValueError("Snapshot symbol must match provenance")
        if self.provider is not self.provenance.provider:
            raise ValueError("Snapshot provider must match provenance")
        if self.source is not self.provenance.source:
            raise ValueError("Snapshot source must match provenance")
        return self


class MarketDataService:
    """Tenant-aware gate between provider verification and internal consumers."""

    def __init__(
        self,
        session: AsyncSession,
        configuration: MarketDataConfiguration,
    ) -> None:
        self.configuration = configuration
        self.memberships = WorkspaceMembershipRepository(session)

    async def snapshot_for(
        self,
        *,
        user: User,
        workspace_id: UUID,
        consumer: MarketDataConsumer,
        verification: ProviderVerification,
        provenance: MarketDataProvenance,
        now: datetime | None = None,
        customer_display: bool = False,
    ) -> MarketSnapshot:
        await self._authorize(user.id, workspace_id)
        if not isinstance(consumer, MarketDataConsumer):
            raise MarketDataAccessError("Unapproved market-data consumer")
        if verification.provider is not self.configuration.provider:
            raise MarketDataUnavailableError("Provider verification mismatch")
        if verification.source is not self.configuration.source:
            raise MarketDataUnavailableError("Provider source mismatch")
        if not verification.authenticated or not verification.provider_verified:
            raise MarketDataUnavailableError("Provider is not verified")

        instrument = next(
            (
                item
                for item in verification.instruments
                if item.canonical is provenance.canonical
            ),
            None,
        )
        if instrument is None or not instrument.mapping_verified:
            raise MarketDataUnavailableError("Instrument is not verified")
        if not instrument.real_time_verified:
            raise MarketDataUnavailableError("Real-time access is not verified")
        if (
            instrument.provider_symbol != provenance.provider_symbol
            or instrument.provider_exchange is None
            or instrument.provider_exchange.upper() != provenance.exchange.upper()
        ):
            raise MarketDataUnavailableError("Instrument provenance mismatch")

        checked_at = now or datetime.now(UTC)
        freshness = provenance.freshness_at(checked_at)
        if freshness is not FreshnessState.FRESH:
            raise MarketDataUnavailableError("Market observation is not fresh")
        if provenance.provider_timestamp is None:
            raise MarketDataUnavailableError("Provider timestamp is missing")

        if customer_display:
            if (
                verification.state
                is not VerificationState.VERIFIED_CUSTOMER_DISPLAY
                or not instrument.available
            ):
                raise MarketDataUnavailableError(
                    "Customer-display entitlement is not verified"
                )
            status = MarketSnapshotStatus.VERIFIED_CUSTOMER_DISPLAY
        else:
            if instrument.state not in {
                VerificationState.VERIFIED_INTERNAL,
                VerificationState.VERIFIED_CUSTOMER_DISPLAY,
            }:
                raise MarketDataUnavailableError(
                    "Internal market access is not verified"
                )
            status = MarketSnapshotStatus.VERIFIED_INTERNAL

        return MarketSnapshot(
            symbol=provenance.canonical,
            provider=provenance.provider,
            source=provenance.source,
            record_type=MarketRecordType.MARKET_STATUS,
            as_of=provenance.provider_timestamp,
            fetched_at=provenance.received_at,
            status=status,
            provenance=provenance,
            freshness=freshness,
        )

    async def _authorize(self, user_id: UUID, workspace_id: UUID) -> None:
        membership = await self.memberships.get_for_user(user_id)
        if membership is None or membership.workspace_id != workspace_id:
            raise MarketDataAccessError("Workspace access is required")
