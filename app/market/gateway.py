"""Provider-agnostic, fail-closed market-data gateway."""

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import User
from app.market.alpha_vantage import AlphaVantageProvider
from app.market.config import CanonicalInstrument, MarketDataConfiguration, MarketDataProviderName
from app.market.provider import MarketDataProvider, MarketDataProvenance
from app.market.service import MarketDataConsumer, MarketDataService, MarketDataUnavailableError, MarketSnapshot
from app.market.twelve_data import TwelveDataProvider
from app.market.verification import MarketVerificationService


class MarketDataProviderRegistry:
    """Explicit adapter registry; adding an adapter does not affect callers."""

    def create(self, configuration: MarketDataConfiguration) -> MarketDataProvider:
        factories = {
            MarketDataProviderName.TWELVE_DATA: TwelveDataProvider,
            MarketDataProviderName.ALPHA_VANTAGE: AlphaVantageProvider,
        }
        factory = factories.get(configuration.provider)
        if factory is None:
            raise MarketDataUnavailableError("Configured market provider is unsupported")
        return factory(configuration)


@dataclass(frozen=True)
class MarketDataGatewayResult:
    price: Decimal
    snapshot: MarketSnapshot
    provenance: MarketDataProvenance


class MarketDataGateway:
    """Only permits an explicitly configured, verified provider path.

    Each configuration is ordered primary then explicit fallbacks. A provider
    failure never relaxes identity, freshness, entitlement, or workspace gates.
    """

    def __init__(self, session: AsyncSession, configurations: tuple[MarketDataConfiguration, ...], registry: MarketDataProviderRegistry | None = None) -> None:
        if not configurations:
            raise ValueError("At least one market provider configuration is required")
        self.session = session
        self.configurations = configurations
        self.registry = registry or MarketDataProviderRegistry()

    async def current_price(self, *, canonical: CanonicalInstrument, user: User, workspace_id: UUID, run_id: UUID) -> MarketDataGatewayResult:
        # This local gate is deliberately before registry/provider creation.
        # An unauthorized request must never cause an upstream provider call.
        await MarketDataService(
            self.session, self.configurations[0]
        ).authorize_request(
            user=user,
            workspace_id=workspace_id,
            consumer=MarketDataConsumer.LEADERSHIP,
            customer_display=True,
        )
        failures: list[Exception] = []
        for configuration in self.configurations:
            try:
                # Fallback providers are independently required to satisfy the
                # same local commercial gate before they can be contacted.
                await MarketDataService(
                    self.session, configuration
                ).authorize_request(
                    user=user,
                    workspace_id=workspace_id,
                    consumer=MarketDataConsumer.LEADERSHIP,
                    customer_display=True,
                )
                provider = self.registry.create(configuration)
                verification, _ = await MarketVerificationService(self.session).verify_and_record(provider, run_id=run_id)
                price, provenance = await provider.current_price(canonical)
                snapshot = await MarketDataService(self.session, configuration).snapshot_for(
                    user=user, workspace_id=workspace_id, consumer=MarketDataConsumer.LEADERSHIP,
                    verification=verification, provenance=provenance, now=provenance.received_at,
                    customer_display=True,
                )
                return MarketDataGatewayResult(price=price, snapshot=snapshot, provenance=provenance)
            except (MarketDataUnavailableError, RuntimeError) as error:
                failures.append(error)
        raise MarketDataUnavailableError("Requested market data is unavailable for customer display") from (failures[-1] if failures else None)
