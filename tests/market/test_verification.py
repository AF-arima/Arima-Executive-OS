import asyncio

import httpx

from app.core.config import Settings
from app.database.models import MarketProviderVerification
from app.market import (
    MarketDataConfiguration,
    MarketVerificationService,
    TwelveDataProvider,
    VerificationState,
)
from tests.auth.conftest import AuthTestContext


def test_not_configured_verification_is_persisted_without_network_or_values(
    auth_context: AuthTestContext,
) -> None:
    def unexpected_request(_: httpx.Request) -> httpx.Response:
        raise AssertionError("missing credentials must prevent network access")

    provider = TwelveDataProvider(
        MarketDataConfiguration.from_settings(
            Settings(_env_file=None, twelve_data_api_key=None)
        ),
        transport=httpx.MockTransport(unexpected_request),
    )

    async def scenario() -> None:
        async with auth_context.session_factory() as session:
            verification, rows = await MarketVerificationService(
                session
            ).verify_and_record(provider)
            await session.commit()

            assert verification.state is VerificationState.NOT_CONFIGURED
            assert len(rows) == 4
            assert {row.canonical for row in rows} == {
                None,
                "XAUUSD",
                "BTCUSD",
                "SPX",
            }
            assert all(not row.authenticated for row in rows)

    asyncio.run(scenario())

    columns = set(MarketProviderVerification.__table__.columns.keys())
    assert not {
        "api_key",
        "raw_payload",
        "price",
        "quote",
        "open",
        "high",
        "low",
        "close",
        "volume",
    } & columns
