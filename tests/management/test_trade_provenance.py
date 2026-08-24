import asyncio
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import select

from app.database.models import FinancialTransaction, SettledTrade, User, Workspace
from app.services.ledger import LedgerLine, LedgerService
from app.database.models import LedgerBucket, LedgerDirection
from tests.auth.helpers import bearer, csrf_headers, grant_role, login_user, register_user


@pytest.fixture
def founder_allowlist(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("FOUNDER_CONTROL_EMAILS", "founder@example.com")
    from app.core.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _seed_usd(context, email: str) -> None:
    async def seed() -> None:
        async with context.session_factory() as session:
            user = await session.scalar(select(User).where(User.email == email))
            assert user is not None
            workspace = await session.scalar(select(Workspace).where(Workspace.owner_id == user.id))
            assert workspace is not None
            ledger = LedgerService(session)
            customer = await ledger.account(workspace_id=workspace.id, user_id=user.id, asset="USD")
            clearing = await ledger.account(workspace_id=workspace.id, user_id=None, asset="USD", account_kind="clearing")
            await ledger.post(
                workspace_id=workspace.id,
                user_id=user.id,
                actor_id=user.id,
                transaction_type="deposit",
                idempotency_key="provenance-seed-deposit-01",
                source="test",
                lines=(
                    LedgerLine(clearing.id, "USD", LedgerDirection.DEBIT, LedgerBucket.AVAILABLE, Decimal("100000")),
                    LedgerLine(customer.id, "USD", LedgerDirection.CREDIT, LedgerBucket.AVAILABLE, Decimal("100000")),
                ),
            )
            await session.commit()

    asyncio.run(seed())


def test_founder_trade_provenance_returns_persisted_links_without_mutation(
    management_context,
    founder_allowlist,
):
    register_user(management_context, "founder@example.com")
    customer = register_user(management_context, "customer@example.com")
    grant_role(management_context, "founder@example.com", "administrator")
    _seed_usd(management_context, "customer@example.com")
    async def workspace_id() -> str:
        async with management_context.session_factory() as session:
            workspace = await session.scalar(select(Workspace).where(Workspace.owner_id == UUID(customer["id"])))
            assert workspace is not None
            return str(workspace.id)

    target_workspace_id = asyncio.run(workspace_id())
    founder = bearer(login_user(management_context, "founder@example.com")["access_token"])
    headers = {**founder, **csrf_headers(management_context)}
    trades_endpoint = f"/api/v1/portfolio/operations/customers/{customer['id']}/trades"
    payload = {
        "side": "buy",
        "base_asset": "BTC",
        "quote_asset": "USD",
        "quantity": "0.000001",
        "price": "100000",
        "fee_amount": "0",
        "executed_at": "2026-08-24T12:00:00Z",
        "reason": "Production accounting smoke test",
        "idempotency_key": "provenance-smoke-trade-01",
        "external_execution_id": "STEP-4A-11-BUY-SMOKE",
    }
    created = management_context.client.post(trades_endpoint, headers=headers, json=payload)
    assert created.status_code == 201, created.text
    trade_id = created.json()["id"]

    async def before_counts() -> tuple[int, int]:
        async with management_context.session_factory() as session:
            return (
                len((await session.scalars(select(SettledTrade))).all()),
                len((await session.scalars(select(FinancialTransaction))).all()),
            )

    before = asyncio.run(before_counts())
    response = management_context.client.get(f"{trades_endpoint}/provenance", headers=founder)
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body) == 1
    item = body[0]
    assert item["settled_trade_id"] == trade_id
    assert item["target_user_id"] == customer["id"]
    assert item["workspace_id"] == target_workspace_id
    assert item["quantity"] == "0.000001000000000000"
    assert item["price"] == "100000.000000000000000000"
    assert item["external_execution_id"] == "STEP-4A-11-BUY-SMOKE"
    assert len(item["financial_transaction_ids"]) == 2
    assert asyncio.run(before_counts()) == before


def test_trade_provenance_is_founder_only_and_empty_history_is_read_only(
    management_context,
    founder_allowlist,
):
    register_user(management_context, "founder@example.com")
    customer = register_user(management_context, "customer@example.com")
    grant_role(management_context, "founder@example.com", "administrator")
    founder = bearer(login_user(management_context, "founder@example.com")["access_token"])
    customer_headers = bearer(login_user(management_context, "customer@example.com")["access_token"])
    endpoint = f"/api/v1/portfolio/operations/customers/{customer['id']}/trades/provenance"

    assert management_context.client.get(endpoint, headers=customer_headers).status_code == 403
    response = management_context.client.get(endpoint, headers=founder)
    assert response.status_code == 200
    assert response.json() == []
