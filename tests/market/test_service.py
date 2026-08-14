import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.core.config import Settings
from app.database.repositories import (
    UserRepository,
    WorkspaceMembershipRepository,
)
from app.market import (
    CanonicalInstrument,
    EntitlementState,
    InstrumentVerification,
    MarketDataAccessError,
    MarketDataConfiguration,
    MarketDataConsumer,
    MarketDataProvenance,
    MarketDataProviderName,
    MarketDataService,
    MarketDataSource,
    MarketDataUnavailableError,
    MarketSnapshotStatus,
    ProviderVerification,
    VerificationState,
)
from tests.auth.conftest import AuthTestContext
from tests.auth.helpers import register_user


def service_configuration() -> MarketDataConfiguration:
    return MarketDataConfiguration.from_settings(
        Settings(
            _env_file=None,
            twelve_data_api_key="server-secret",
            market_data_real_time_entitled=True,
            market_data_entitlement_reference="internal-approval-id",
        )
    )


def verification() -> ProviderVerification:
    return ProviderVerification(
        provider=MarketDataProviderName.TWELVE_DATA,
        source=MarketDataSource.TWELVE_DATA,
        state=VerificationState.VERIFIED_INTERNAL,
        configured=True,
        authenticated=True,
        provider_verified=True,
        checked_at=datetime(2026, 8, 12, tzinfo=timezone.utc),
        instruments=(
            InstrumentVerification(
                canonical=CanonicalInstrument.BTCUSD,
                state=VerificationState.VERIFIED_INTERNAL,
                provider_verified=True,
                symbol_verified=True,
                source_verified=True,
                real_time_verified=True,
                catalog_access_verified=True,
                provider_symbol="BTC/USD",
                provider_exchange="Coinbase Pro",
                provider_instrument_type="Digital Currency",
                provider_currency="USD",
                entitlement_state=(
                    EntitlementState.INTERNAL_NON_DISPLAY_ONLY
                ),
            ),
        ),
    )


def provenance(*, age_seconds: int = 1) -> MarketDataProvenance:
    received_at = datetime(2026, 8, 12, 12, tzinfo=timezone.utc)
    return MarketDataProvenance(
        canonical=CanonicalInstrument.BTCUSD,
        provider=MarketDataProviderName.TWELVE_DATA,
        source=MarketDataSource.TWELVE_DATA,
        provider_symbol="BTC/USD",
        exchange="Coinbase Pro",
        provider_timestamp=received_at - timedelta(seconds=age_seconds),
        received_at=received_at,
        stale_after_seconds=120,
    )


def test_internal_snapshot_is_tenant_bound_non_price_and_fresh(
    auth_context: AuthTestContext,
) -> None:
    email = "market-service@example.com"
    register_user(auth_context, email)

    async def scenario() -> None:
        async with auth_context.session_factory() as session:
            user = await UserRepository(session).get_by_email(email)
            assert user is not None
            membership = await WorkspaceMembershipRepository(
                session
            ).get_for_user(user.id)
            assert membership is not None
            snapshot = await MarketDataService(
                session, service_configuration()
            ).snapshot_for(
                user=user,
                workspace_id=membership.workspace_id,
                consumer=MarketDataConsumer.Q_RESEARCH,
                verification=verification(),
                provenance=provenance(),
                now=datetime(2026, 8, 12, 12, tzinfo=timezone.utc),
            )

            assert snapshot.status is MarketSnapshotStatus.VERIFIED_INTERNAL
            serialized = snapshot.model_dump_json()
            assert "price" not in serialized.lower()
            assert "server-secret" not in serialized
            assert "internal-approval-id" not in serialized

    asyncio.run(scenario())


def test_cross_tenant_workspace_is_rejected(
    auth_context: AuthTestContext,
) -> None:
    email = "market-cross-tenant@example.com"
    register_user(auth_context, email)

    async def scenario() -> None:
        async with auth_context.session_factory() as session:
            user = await UserRepository(session).get_by_email(email)
            assert user is not None
            service = MarketDataService(session, service_configuration())
            with pytest.raises(MarketDataAccessError):
                await service.snapshot_for(
                    user=user,
                    workspace_id=uuid4(),
                    consumer=MarketDataConsumer.Q_LAB,
                    verification=verification(),
                    provenance=provenance(),
                    now=datetime(2026, 8, 12, 12, tzinfo=timezone.utc),
                )

    asyncio.run(scenario())


def test_stale_or_missing_timestamp_and_customer_display_fail_closed(
    auth_context: AuthTestContext,
) -> None:
    email = "market-fail-closed@example.com"
    register_user(auth_context, email)

    async def scenario() -> None:
        async with auth_context.session_factory() as session:
            user = await UserRepository(session).get_by_email(email)
            assert user is not None
            membership = await WorkspaceMembershipRepository(
                session
            ).get_for_user(user.id)
            assert membership is not None
            service = MarketDataService(session, service_configuration())
            common = {
                "user": user,
                "workspace_id": membership.workspace_id,
                "consumer": MarketDataConsumer.LEADERSHIP,
                "verification": verification(),
                "now": datetime(2026, 8, 12, 12, tzinfo=timezone.utc),
            }
            with pytest.raises(MarketDataUnavailableError, match="fresh"):
                await service.snapshot_for(
                    **common,
                    provenance=provenance(age_seconds=121),
                )
            missing = provenance().model_copy(
                update={"provider_timestamp": None}
            )
            with pytest.raises(MarketDataUnavailableError, match="fresh"):
                await service.snapshot_for(**common, provenance=missing)
            with pytest.raises(
                MarketDataUnavailableError,
                match="Customer-display entitlement",
            ):
                await service.snapshot_for(
                    **common,
                    provenance=provenance(),
                    customer_display=True,
                )

    asyncio.run(scenario())
