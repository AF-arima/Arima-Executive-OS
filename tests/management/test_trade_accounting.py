import asyncio
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app.database.models import AuditEntity, AuditLog, FinancialTransaction, LedgerBucket, LedgerDirection, LedgerEntry, PortfolioPosition, SettledTrade, SettledTradeStatus, User, Workspace
from app.services.ledger import LedgerLine, LedgerService
from tests.auth.helpers import bearer, csrf_headers, grant_role, login_user, register_user


@pytest.fixture
def founder_allowlist(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("FOUNDER_CONTROL_EMAILS", "founder@example.com")
    from app.core.config import get_settings
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _seed_usd(context, email: str, amount: Decimal = Decimal("100000")) -> str:
    async def seed() -> str:
        async with context.session_factory() as session:
            user = await session.scalar(select(User).where(User.email == email))
            assert user is not None
            workspace = await session.scalar(select(Workspace).where(Workspace.owner_id == user.id))
            assert workspace is not None
            ledger = LedgerService(session)
            customer = await ledger.account(workspace_id=workspace.id, user_id=user.id, asset="USD")
            clearing = await ledger.account(workspace_id=workspace.id, user_id=None, asset="USD", account_kind="clearing")
            await ledger.post(workspace_id=workspace.id, user_id=user.id, actor_id=user.id, transaction_type="deposit", idempotency_key="seed-deposit-0000001", source="test", lines=(LedgerLine(clearing.id, "USD", LedgerDirection.DEBIT, LedgerBucket.AVAILABLE, amount), LedgerLine(customer.id, "USD", LedgerDirection.CREDIT, LedgerBucket.AVAILABLE, amount)))
            await session.commit()
            return str(user.id)
    return asyncio.run(seed())


def _debit_customer_asset(context, email: str, asset: str, amount: Decimal, key: str) -> None:
    async def drain() -> None:
        async with context.session_factory() as session:
            user = await session.scalar(select(User).where(User.email == email))
            assert user is not None
            workspace = await session.scalar(select(Workspace).where(Workspace.owner_id == user.id))
            assert workspace is not None
            ledger = LedgerService(session)
            customer = await ledger.account(workspace_id=workspace.id, user_id=user.id, asset=asset)
            clearing = await ledger.account(workspace_id=workspace.id, user_id=None, asset=asset, account_kind="clearing")
            await ledger.post(
                workspace_id=workspace.id, user_id=user.id, actor_id=user.id,
                transaction_type="test_debit", idempotency_key=key, source="test",
                lines=(
                    LedgerLine(customer.id, asset, LedgerDirection.DEBIT, LedgerBucket.AVAILABLE, amount),
                    LedgerLine(clearing.id, asset, LedgerDirection.CREDIT, LedgerBucket.AVAILABLE, amount),
                ),
            )
            await session.commit()

    asyncio.run(drain())


def test_founder_buy_settles_two_legs_and_dashboard_reads_persisted_state(management_context, founder_allowlist):
    register_user(management_context, "founder@example.com")
    customer = register_user(management_context, "customer@example.com")
    grant_role(management_context, "founder@example.com", "administrator")
    _seed_usd(management_context, "customer@example.com")
    founder = bearer(login_user(management_context, "founder@example.com")["access_token"])
    customer_headers = bearer(login_user(management_context, "customer@example.com")["access_token"])
    endpoint = f"/api/v1/portfolio/operations/customers/{customer['id']}/trades"
    payload = {"side": "buy", "base_asset": "BTC", "quote_asset": "USD", "quantity": "0.5", "price": "10000", "fee_amount": "10", "executed_at": "2026-08-24T12:00:00Z", "reason": "Founder manual reconciliation", "idempotency_key": "trade-buy-00000001"}
    missing_csrf = management_context.client.post(endpoint, headers=founder, json=payload)
    response = management_context.client.post(endpoint, headers={**founder, **csrf_headers(management_context)}, json=payload)
    assert missing_csrf.status_code == 403
    assert response.status_code == 201, response.text
    trade = response.json()
    assert trade["quote_value"] == "5000.000000000000000000"

    replay = management_context.client.post(endpoint, headers={**founder, **csrf_headers(management_context)}, json=payload)
    assert replay.status_code == 201
    assert replay.json()["id"] == trade["id"]
    conflict = management_context.client.post(endpoint, headers={**founder, **csrf_headers(management_context)}, json={**payload, "price": "11000"})
    assert conflict.status_code == 409

    portfolio = management_context.client.get("/api/v1/portfolio", headers=customer_headers)
    assert portfolio.status_code == 200
    body = portfolio.json()
    assert next(item for item in body["positions"] if item["asset"] == "BTC")["quantity"] == "0.500000000000000000"
    assert next(item for item in body["balances"] if item["asset"] == "USD")["available_balance"] == "94990.000000000000000000"
    assert len(body["recent_ledger_activity"]) == 3

    async def persisted():
        async with management_context.session_factory() as session:
            trade_id = UUID(trade["id"])
            trade_row = await session.scalar(select(SettledTrade).where(SettledTrade.id == trade_id))
            legs = list((await session.scalars(select(FinancialTransaction).where(FinancialTransaction.trade_id == trade_id))).all())
            audit = await session.scalar(select(AuditLog).where(AuditLog.entity == AuditEntity.TRADE))
            return trade_row, legs, audit
    trade_row, legs, audit = asyncio.run(persisted())
    assert trade_row is not None and len(legs) == 2 and audit is not None


def test_trade_authorization_and_atomic_failure(management_context, founder_allowlist, monkeypatch: pytest.MonkeyPatch):
    register_user(management_context, "founder@example.com")
    customer = register_user(management_context, "customer@example.com")
    register_user(management_context, "other-admin@example.com")
    grant_role(management_context, "founder@example.com", "administrator")
    grant_role(management_context, "other-admin@example.com", "administrator")
    _seed_usd(management_context, "customer@example.com")
    normal = bearer(login_user(management_context, "customer@example.com")["access_token"])
    other_admin = bearer(login_user(management_context, "other-admin@example.com")["access_token"])
    endpoint = f"/api/v1/portfolio/operations/customers/{customer['id']}/trades"
    payload = {"side": "buy", "base_asset": "BTC", "quote_asset": "USD", "quantity": "0.5", "price": "10000", "fee_amount": "10", "executed_at": "2026-08-24T12:00:00Z", "reason": "test", "idempotency_key": "trade-auth-00000001"}
    assert management_context.client.post(endpoint, headers={**normal, **csrf_headers(management_context)}, json=payload).status_code == 403
    assert management_context.client.post(endpoint, headers={**other_admin, **csrf_headers(management_context)}, json=payload).status_code == 403

    from app.services import trade_accounting
    original_post = trade_accounting.LedgerService.post
    calls = 0
    async def fail_second(self, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("forced ledger failure")
        return await original_post(self, **kwargs)
    monkeypatch.setattr(trade_accounting.LedgerService, "post", fail_second)
    founder = bearer(login_user(management_context, "founder@example.com")["access_token"])
    invalid_target = f"/api/v1/portfolio/operations/customers/{uuid4()}/trades"
    assert management_context.client.post(invalid_target, headers={**founder, **csrf_headers(management_context)}, json=payload).status_code == 404
    with pytest.raises(RuntimeError, match="forced ledger failure"):
        management_context.client.post(endpoint, headers={**founder, **csrf_headers(management_context)}, json=payload)

    async def state():
        async with management_context.session_factory() as session:
            return len((await session.scalars(select(SettledTrade))).all()), len((await session.scalars(select(FinancialTransaction).where(FinancialTransaction.source == "manual_trade"))).all()), len((await session.scalars(select(PortfolioPosition))).all())
    assert asyncio.run(state()) == (0, 0, 0)


def test_sell_and_reversal_restore_authoritative_state(management_context, founder_allowlist):
    register_user(management_context, "founder@example.com")
    customer = register_user(management_context, "customer@example.com")
    grant_role(management_context, "founder@example.com", "administrator")
    _seed_usd(management_context, "customer@example.com")
    founder = bearer(login_user(management_context, "founder@example.com")["access_token"])
    headers = {**founder, **csrf_headers(management_context)}
    endpoint = f"/api/v1/portfolio/operations/customers/{customer['id']}/trades"
    buy = {
        "side": "buy", "base_asset": "BTC", "quote_asset": "USD", "quantity": "0.5",
        "price": "10000", "fee_amount": "10", "executed_at": "2026-08-24T12:00:00Z",
        "reason": "Initial manual reconciliation", "idempotency_key": "trade-reverse-buy-01",
    }
    assert management_context.client.post(endpoint, headers=headers, json=buy).status_code == 201
    sell = {
        **buy, "side": "sell", "quantity": "0.2", "price": "12000", "fee_amount": "20",
        "reason": "Correction test sell", "idempotency_key": "trade-reverse-sell-01",
    }
    sell_response = management_context.client.post(endpoint, headers=headers, json=sell)
    assert sell_response.status_code == 201, sell_response.text
    sell_trade = sell_response.json()
    reverse_endpoint = f"{endpoint}/{sell_trade['id']}/reverse"
    assert management_context.client.post(reverse_endpoint, headers=founder, json={"reason": "Reverse mistaken manual sell", "idempotency_key": "trade-reversal-000001"}).status_code == 403
    reversal = management_context.client.post(reverse_endpoint, headers=headers, json={"reason": "Reverse mistaken manual sell", "idempotency_key": "trade-reversal-000001"})
    assert reversal.status_code == 200, reversal.text
    assert reversal.json()["reversal_of_id"] == sell_trade["id"]
    replay = management_context.client.post(reverse_endpoint, headers=headers, json={"reason": "Reverse mistaken manual sell", "idempotency_key": "trade-reversal-000001"})
    assert replay.status_code == 200 and replay.json()["id"] == reversal.json()["id"]
    conflict = management_context.client.post(reverse_endpoint, headers=headers, json={"reason": "Different correction reason", "idempotency_key": "trade-reversal-000001"})
    assert conflict.status_code == 409
    customer_headers = bearer(login_user(management_context, "customer@example.com")["access_token"])
    assert management_context.client.post(reverse_endpoint, headers={**customer_headers, **csrf_headers(management_context)}, json={"reason": "Customer must not reverse", "idempotency_key": "trade-customer-reversal-01"}).status_code == 403

    portfolio = management_context.client.get("/api/v1/portfolio", headers=customer_headers)
    assert portfolio.status_code == 200
    body = portfolio.json()
    assert next(item for item in body["positions"] if item["asset"] == "BTC")["quantity"] == "0.500000000000000000"
    assert next(item for item in body["balances"] if item["asset"] == "USD")["available_balance"] == "94990.000000000000000000"

    async def persisted():
        async with management_context.session_factory() as session:
            original = await session.get(SettledTrade, UUID(sell_trade["id"]))
            return original.status, len((await session.scalars(select(FinancialTransaction).where(FinancialTransaction.trade_id == UUID(reversal.json()["id"])))).all())

    status, reversal_legs = asyncio.run(persisted())
    assert status == SettledTradeStatus.REVERSED.value
    assert reversal_legs == 2


def test_reversal_rejects_insufficient_base_position_without_mutation(management_context, founder_allowlist):
    register_user(management_context, "founder@example.com")
    customer = register_user(management_context, "customer@example.com")
    grant_role(management_context, "founder@example.com", "administrator")
    _seed_usd(management_context, "customer@example.com")
    founder = bearer(login_user(management_context, "founder@example.com")["access_token"])
    headers = {**founder, **csrf_headers(management_context)}
    endpoint = f"/api/v1/portfolio/operations/customers/{customer['id']}/trades"
    buy = {"side": "buy", "base_asset": "BTC", "quote_asset": "USD", "quantity": "0.5", "price": "10000", "fee_amount": "10", "executed_at": "2026-08-24T12:00:00Z", "reason": "Position safety test", "idempotency_key": "trade-position-safety-01"}
    buy_response = management_context.client.post(endpoint, headers=headers, json=buy)
    assert buy_response.status_code == 201
    sell = {**buy, "side": "sell", "price": "9000", "fee_amount": "0", "reason": "Consume position", "idempotency_key": "trade-position-consume-01"}
    assert management_context.client.post(endpoint, headers=headers, json=sell).status_code == 201
    trade_id = buy_response.json()["id"]
    reverse_endpoint = f"{endpoint}/{trade_id}/reverse"
    rejected = management_context.client.post(reverse_endpoint, headers=headers, json={"reason": "Cannot reverse consumed position", "idempotency_key": "trade-position-reversal-01"})
    assert rejected.status_code == 409

    async def state():
        async with management_context.session_factory() as session:
            original = await session.get(SettledTrade, UUID(trade_id))
            reversals = list((await session.scalars(select(SettledTrade).where(SettledTrade.reversal_of_id == UUID(trade_id)))).all())
            return original.status, reversals

    status, reversals = asyncio.run(state())
    assert status == SettledTradeStatus.RECORDED.value and reversals == []


def test_reversal_rejects_insufficient_quote_balance_including_fee_without_mutation(management_context, founder_allowlist):
    register_user(management_context, "founder@example.com")
    customer = register_user(management_context, "customer@example.com")
    grant_role(management_context, "founder@example.com", "administrator")
    _seed_usd(management_context, "customer@example.com")
    founder = bearer(login_user(management_context, "founder@example.com")["access_token"])
    headers = {**founder, **csrf_headers(management_context)}
    endpoint = f"/api/v1/portfolio/operations/customers/{customer['id']}/trades"
    buy = {"side": "buy", "base_asset": "BTC", "quote_asset": "USD", "quantity": "0.5", "price": "10000", "fee_amount": "10", "executed_at": "2026-08-24T12:00:00Z", "reason": "Quote safety setup", "idempotency_key": "trade-quote-safety-buy"}
    assert management_context.client.post(endpoint, headers=headers, json=buy).status_code == 201
    sell = {**buy, "side": "sell", "quantity": "0.2", "price": "12000", "fee_amount": "90", "reason": "Fee-sensitive sell", "idempotency_key": "trade-quote-safety-sell"}
    sell_response = management_context.client.post(endpoint, headers=headers, json=sell)
    assert sell_response.status_code == 201
    _debit_customer_asset(management_context, "customer@example.com", "USD", Decimal("97300"), "drain-quote-before-reversal")
    trade_id = sell_response.json()["id"]
    reverse_endpoint = f"{endpoint}/{trade_id}/reverse"
    rejected = management_context.client.post(reverse_endpoint, headers=headers, json={"reason": "Quote balance is insufficient", "idempotency_key": "trade-quote-reversal-01"})
    assert rejected.status_code == 409

    async def state():
        async with management_context.session_factory() as session:
            original = await session.get(SettledTrade, UUID(trade_id))
            reversal_count = len((await session.scalars(select(SettledTrade).where(SettledTrade.reversal_of_id == UUID(trade_id)))).all())
            transactions = list((await session.scalars(select(FinancialTransaction).where(FinancialTransaction.trade_id == UUID(trade_id)))).all())
            entry_count = len((await session.scalars(select(LedgerEntry).where(LedgerEntry.transaction_id.in_([transaction.id for transaction in transactions])))).all())
            audit_count = len((await session.scalars(select(AuditLog).where(AuditLog.event_type == "FOUNDER_TRADE_REVERSED"))).all())
            return original.status, reversal_count, len(transactions), entry_count, audit_count

    status, reversal_count, transaction_count, entry_count, audit_count = asyncio.run(state())
    assert (status, reversal_count, transaction_count, entry_count, audit_count) == (SettledTradeStatus.RECORDED.value, 0, 2, 4, 0)
