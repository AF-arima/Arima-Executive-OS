from app.main import app
from tests.auth.conftest import AuthTestContext
from tests.auth.helpers import bearer, login_user, register_user


def test_market_availability_requires_authentication(
    auth_context: AuthTestContext,
) -> None:
    response = auth_context.client.get("/api/v1/market/availability")

    assert response.status_code == 401


def test_market_availability_reports_no_verified_provider(
    auth_context: AuthTestContext,
) -> None:
    email = "market-availability@example.com"
    register_user(auth_context, email)
    token = login_user(auth_context, email)["access_token"]

    response = auth_context.client.get(
        "/api/v1/market/availability",
        headers=bearer(token),
    )

    assert response.status_code == 200
    assert response.json() == {
        "symbols": [
            {
                "symbol": symbol,
                "available": False,
                "provider": None,
                "reason": "No verified market data provider is configured.",
            }
            for symbol in ("XAUUSD", "BTCUSD", "SPX")
        ]
    }


def test_market_availability_does_not_cross_tenant_boundaries(
    auth_context: AuthTestContext,
) -> None:
    tokens: list[object] = []
    for email in ("tenant-one@example.com", "tenant-two@example.com"):
        register_user(auth_context, email)
        tokens.append(login_user(auth_context, email)["access_token"])

    responses = [
        auth_context.client.get(
            "/api/v1/market/availability",
            headers=bearer(token),
        )
        for token in tokens
    ]

    assert [response.status_code for response in responses] == [200, 200]
    assert responses[0].json() == responses[1].json()
    serialized = responses[0].text + responses[1].text
    assert "tenant-one" not in serialized
    assert "tenant-two" not in serialized
    assert "api_key" not in serialized


def test_public_availability_never_returns_prices_or_internal_diagnostics(
    auth_context: AuthTestContext,
) -> None:
    email = "market-public-contract@example.com"
    register_user(auth_context, email)
    token = login_user(auth_context, email)["access_token"]

    response = auth_context.client.get(
        "/api/v1/market/availability",
        headers=bearer(token),
    )

    assert response.status_code == 200
    serialized = response.text.lower()
    for forbidden in (
        "price",
        "quote",
        "candle",
        "api_key",
        "raw_payload",
        "customer_display_entitled",
        "workspace_id",
    ):
        assert forbidden not in serialized


def test_market_router_exposes_only_authenticated_non_price_availability() -> None:
    market_routes = {
        path: frozenset(methods)
        for path, methods in app.openapi()["paths"].items()
        if path.startswith("/api/v1/market")
    }

    assert market_routes == {
        "/api/v1/market/availability": frozenset({"get"})
    }
