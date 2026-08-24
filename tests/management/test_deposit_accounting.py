import asyncio
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app.database.models import (
    AuditLog,
    FinancialAccount,
    FinancialTransaction,
    LedgerEntry,
    PortfolioPosition,
    SettledTrade,
)
from app.services.ledger import LedgerService
from tests.auth.helpers import bearer, csrf_headers, grant_role, login_user, register_user


@pytest.fixture
def founder_allowlist(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("FOUNDER_CONTROL_EMAILS", "founder@example.com")
    from app.core.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _setup(context):
    register_user(context, "founder@example.com")
    customer = register_user(context, "customer@example.com")
    other = register_user(context, "other@example.com")
    grant_role(context, "founder@example.com", "administrator")
    founder = bearer(login_user(context, "founder@example.com")["access_token"])
    customer_headers = bearer(login_user(context, "customer@example.com")["access_token"])
    other_headers = bearer(login_user(context, "other@example.com")["access_token"])
    return customer, other, founder, customer_headers, other_headers


def _payload(**overrides):
    payload = {
        "asset": "USD",
        "amount": "1.00",
        "reference": "STEP-4A-10-TEST-DEPOSIT",
        "reason": "Founder manual funding for accounting smoke test",
        "idempotency_key": "deposit-test-key-000001",
    }
    payload.update(overrides)
    return payload


async def _financial_row_counts(context):
    async with context.session_factory() as session:
        counts = []
        for model in (FinancialAccount, FinancialTransaction, LedgerEntry, AuditLog):
            counts.append(len((await session.scalars(select(model))).all()))
        return tuple(counts)


def test_founder_deposit_is_balanced_idempotent_and_authoritative(management_context, founder_allowlist):
    customer, _, founder, customer_headers, _ = _setup(management_context)
    endpoint = f"/api/v1/portfolio/operations/customers/{customer['id']}/deposits"
    headers = {**founder, **csrf_headers(management_context)}

    missing_csrf = management_context.client.post(endpoint, headers=founder, json=_payload())
    response = management_context.client.post(endpoint, headers=headers, json=_payload())
    assert missing_csrf.status_code == 403
    assert response.status_code == 201, response.text
    deposit = response.json()
    replay = management_context.client.post(endpoint, headers=headers, json=_payload())
    assert replay.status_code == 201 and replay.json()["id"] == deposit["id"]

    conflict = management_context.client.post(endpoint, headers=headers, json=_payload(amount="2.00"))
    assert conflict.status_code == 409

    async def state():
        async with management_context.session_factory() as session:
            transaction = await session.get(FinancialTransaction, UUID(deposit["financial_transaction_id"]))
            entries = list((await session.scalars(select(LedgerEntry).where(LedgerEntry.transaction_id == transaction.id))).all())
            account = await session.get(FinancialAccount, UUID(deposit["financial_account_id"]))
            balance = await LedgerService(session).balance(account_id=account.id, asset="USD")
            audits = list((await session.scalars(select(AuditLog).where(AuditLog.event_type == "FOUNDER_MANUAL_DEPOSIT_RECORDED"))).all())
            return transaction, entries, balance, audits

    transaction, entries, balance, audits = asyncio.run(state())
    assert transaction is not None and transaction.source == "founder_manual_deposit"
    assert len(entries) == 2
    assert sum((entry.amount for entry in entries if entry.direction == "debit"), Decimal("0")) == Decimal("1.000000000000000000")
    assert sum((entry.amount for entry in entries if entry.direction == "credit"), Decimal("0")) == Decimal("1.000000000000000000")
    assert balance.authoritative_balance == Decimal("1.000000000000000000")
    assert balance.available_balance == Decimal("1.000000000000000000")
    dashboard = management_context.client.get("/api/v1/portfolio", headers=customer_headers)
    assert dashboard.status_code == 200
    assert next(item for item in dashboard.json()["balances"] if item["asset"] == "USD")["available_balance"] == "1.000000000000000000"
    assert len(audits) == 1
    assert audits[0].event_metadata["target_user_id"] == customer["id"]
    assert audits[0].event_metadata["reason"] == _payload()["reason"]

    async def no_trade_or_position():
        async with management_context.session_factory() as session:
            return len((await session.scalars(select(SettledTrade))).all()), len((await session.scalars(select(PortfolioPosition))).all())

    assert asyncio.run(no_trade_or_position()) == (0, 0)


def test_deposit_is_founder_only_and_target_scoped(management_context, founder_allowlist):
    customer, other, founder, customer_headers, other_headers = _setup(management_context)
    endpoint = f"/api/v1/portfolio/operations/customers/{customer['id']}/deposits"
    payload = _payload(idempotency_key="deposit-auth-key-000001")
    assert management_context.client.post(endpoint, headers={**customer_headers, **csrf_headers(management_context)}, json=payload).status_code == 403
    assert management_context.client.post(endpoint, headers={**other_headers, **csrf_headers(management_context)}, json=payload).status_code == 403
    invalid = f"/api/v1/portfolio/operations/customers/{uuid4()}/deposits"
    assert management_context.client.post(invalid, headers={**founder, **csrf_headers(management_context)}, json=payload).status_code == 404

    async def count_accounts():
        async with management_context.session_factory() as session:
            return len((await session.scalars(select(FinancialAccount))).all())

    assert asyncio.run(count_accounts()) == 0
    assert other["id"] != customer["id"]


@pytest.mark.parametrize(
    ("field", "value"),
    [("amount", "0"), ("amount", "-1"), ("asset", "EUR"), ("reason", ""), ("reference", ""), ("idempotency_key", "short")],
)
def test_deposit_rejects_invalid_contract_before_persistence(management_context, founder_allowlist, field, value):
    customer, _, founder, _, _ = _setup(management_context)
    endpoint = f"/api/v1/portfolio/operations/customers/{customer['id']}/deposits"
    response = management_context.client.post(endpoint, headers={**founder, **csrf_headers(management_context)}, json=_payload(**{field: value}))
    assert response.status_code in {409, 422}

    assert asyncio.run(_financial_row_counts(management_context)) == (0, 0, 0, 0)


def test_failed_deposit_rolls_back_all_financial_state(management_context, founder_allowlist, monkeypatch: pytest.MonkeyPatch):
    customer, _, founder, _, _ = _setup(management_context)
    endpoint = f"/api/v1/portfolio/operations/customers/{customer['id']}/deposits"
    from app.services import deposit_accounting

    async def fail_post(self, **kwargs):
        raise RuntimeError("forced deposit failure")

    monkeypatch.setattr(deposit_accounting.LedgerService, "post", fail_post)
    with pytest.raises(RuntimeError, match="forced deposit failure"):
        management_context.client.post(endpoint, headers={**founder, **csrf_headers(management_context)}, json=_payload(idempotency_key="deposit-rollback-key"))

    assert asyncio.run(_financial_row_counts(management_context)) == (0, 0, 0, 0)
