import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.database.models import (
    FinancialAccount,
    FinancialTransaction,
    LedgerEntry,
    Portfolio,
    PortfolioPosition,
    SettledTrade,
)
from tests.auth.helpers import bearer, grant_role, login_user, register_user


@pytest.fixture
def founder_allowlist(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("FOUNDER_CONTROL_EMAILS", "founder@example.com")
    from app.core.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_founder_financial_state_is_strictly_read_only(
    management_context,
    founder_allowlist,
):
    register_user(management_context, "founder@example.com")
    customer = register_user(management_context, "customer@example.com")
    grant_role(management_context, "founder@example.com", "administrator")
    founder = bearer(login_user(management_context, "founder@example.com")["access_token"])

    async def before_counts() -> dict[str, int]:
        async with management_context.session_factory() as session:
            return {
                "accounts": int(await session.scalar(select(func.count(FinancialAccount.id))) or 0),
                "portfolios": int(await session.scalar(select(func.count(Portfolio.id))) or 0),
                "positions": int(await session.scalar(select(func.count(PortfolioPosition.id))) or 0),
                "entries": int(await session.scalar(select(func.count(LedgerEntry.id))) or 0),
                "transactions": int(await session.scalar(select(func.count(FinancialTransaction.id))) or 0),
                "trades": int(await session.scalar(select(func.count(SettledTrade.id))) or 0),
            }

    before = asyncio.run(before_counts())
    response = management_context.client.get(
        f"/api/v1/portfolio/operations/customers/{customer['id']}/financial-state",
        headers=founder,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["user_id"] == customer["id"]
    assert body["portfolio_id"] is None
    assert body["financial_accounts"] == []
    assert body["positions"] == []
    assert body["ledger_activity_count"] == 0
    assert body["settled_trade_count"] == 0
    assert asyncio.run(before_counts()) == before


def test_customer_cannot_use_founder_financial_state_inspection(
    management_context,
    founder_allowlist,
):
    register_user(management_context, "customer@example.com")
    other = register_user(management_context, "other@example.com")
    customer_headers = bearer(login_user(management_context, "customer@example.com")["access_token"])
    response = management_context.client.get(
        f"/api/v1/portfolio/operations/customers/{other['id']}/financial-state",
        headers=customer_headers,
    )
    assert response.status_code == 403


def test_founder_financial_state_rejects_invalid_target_scope(
    management_context,
    founder_allowlist,
):
    register_user(management_context, "founder@example.com")
    grant_role(management_context, "founder@example.com", "administrator")
    founder = bearer(login_user(management_context, "founder@example.com")["access_token"])
    response = management_context.client.get(
        f"/api/v1/portfolio/operations/customers/{uuid4()}/financial-state",
        headers=founder,
    )
    assert response.status_code == 404
