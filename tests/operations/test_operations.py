from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.exc import OperationalError

from app.database.models import LedgerBucket, LedgerDirection, Tenant, User, Workspace, WorkspaceMembership, WithdrawalCircuitState, WithdrawalCircuitBreaker, WithdrawalRequest, WithdrawalState
from app.schemas.operations import WithdrawalRequestCreate
from app.services.operations import (
    InsufficientBalanceError,
    OperationsError,
    WithdrawalService,
    CustomerSupportService,
)
from app.services.ledger import LedgerLine, LedgerService
from app.services.operations import WithdrawalCircuitOpenError
from app.services.risk_contract import CircuitStateUnavailableError
from tests.database.helpers import sqlite_session


class Email:
    def __init__(self):
        self.calls = []

    async def send_withdrawal_received(self, **kwargs):
        self.calls.append(kwargs)


def request(**overrides):
    values = {
        "first_name": "Test",
        "last_name": "User",
        "amount": "0.5",
        "currency": "ETH",
        "destination_wallet_address": "0x0000000000000000000000000000000000000001",
        "network": "Ethereum Mainnet",
        "confirmation": True,
        "risk_acknowledgement": True,
        "idempotency_key": "request-" + uuid4().hex,
    }
    values.update(overrides)
    return WithdrawalRequestCreate.model_validate(values)


@pytest.mark.asyncio
async def test_withdrawal_is_validated_and_notification_is_safe():
    async with sqlite_session() as session:
        user = User(email="user@example.com", hashed_password="not-used", first_name="Test", last_name="User", is_verified=True)
        workspace = Workspace(name="User workspace", owner=user)
        clearing = User(email="clearing@example.com", hashed_password="not-used", first_name="Clearing", last_name="Account", is_verified=True)
        session.add_all([user, workspace, clearing])
        await session.flush()
        session.add(WithdrawalCircuitBreaker(workspace_id=workspace.id, state=WithdrawalCircuitState.ENABLED.value))
        await session.flush()
        customer_account = await LedgerService(session).account(workspace_id=workspace.id, user_id=user.id, asset="ETH")
        clearing_account = await LedgerService(session).account(workspace_id=workspace.id, user_id=clearing.id, asset="ETH")
        await LedgerService(session).post(
            workspace_id=workspace.id, user_id=user.id, actor_id=user.id,
            transaction_type="deposit", idempotency_key="test-deposit", source="test",
            lines=(LedgerLine(clearing_account.id, "ETH", LedgerDirection.DEBIT, LedgerBucket.AVAILABLE, Decimal("2")), LedgerLine(customer_account.id, "ETH", LedgerDirection.CREDIT, LedgerBucket.AVAILABLE, Decimal("2"))),
        )
        email = Email()
        item = await WithdrawalService(session, email_service=email).create(request(), user)
        assert item.state == WithdrawalState.UNDER_REVIEW.value
        assert item.notification_status == "sent"
        assert email.calls[0]["masked_wallet"] == "0x0000…0001"
        assert "access" not in str(email.calls[0]).lower()


@pytest.mark.asyncio
async def test_duplicate_withdrawal_request_has_one_financial_effect():
    async with sqlite_session() as session:
        user = User(email="duplicate@example.com", hashed_password="not-used", first_name="Test", last_name="User", is_verified=True)
        workspace = Workspace(name="Duplicate workspace", owner=user)
        clearing = User(email="duplicate-clearing@example.com", hashed_password="not-used", first_name="Clearing", last_name="Account", is_verified=True)
        session.add_all([user, workspace, clearing])
        await session.flush()
        session.add(WithdrawalCircuitBreaker(workspace_id=workspace.id, state=WithdrawalCircuitState.ENABLED.value))
        await session.flush()
        ledger = LedgerService(session)
        customer = await ledger.account(workspace_id=workspace.id, user_id=user.id, asset="ETH")
        contra = await ledger.account(workspace_id=workspace.id, user_id=clearing.id, asset="ETH")
        await ledger.post(
            workspace_id=workspace.id, user_id=user.id, actor_id=user.id,
            transaction_type="deposit", idempotency_key="duplicate-deposit", source="test",
            lines=(LedgerLine(contra.id, "ETH", LedgerDirection.DEBIT, LedgerBucket.AVAILABLE, Decimal("2")), LedgerLine(customer.id, "ETH", LedgerDirection.CREDIT, LedgerBucket.AVAILABLE, Decimal("2"))),
        )
        data = request(idempotency_key="duplicate-withdrawal")
        service = WithdrawalService(session)
        first = await service.create(data, user)
        second = await service.create(data, user)
        assert second.id == first.id
        assert (await ledger.balance(account_id=customer.id, asset="ETH")).reserved_balance == Decimal("0.5")


@pytest.mark.asyncio
async def test_withdrawal_uses_authoritative_zero_balance():
    async with sqlite_session() as session:
        user = User(email="user@example.com", hashed_password="not-used", first_name="Test", last_name="User", is_verified=True)
        workspace = Workspace(name="User workspace", owner=user)
        session.add_all([user, workspace])
        await session.flush()
        session.add(WithdrawalCircuitBreaker(workspace_id=workspace.id, state=WithdrawalCircuitState.ENABLED.value))
        await session.flush()
        with pytest.raises(InsufficientBalanceError):
            await WithdrawalService(session).create(request(), user)


@pytest.mark.asyncio
async def test_withdrawal_fails_closed_when_circuit_state_is_missing():
    async with sqlite_session() as session:
        user = User(email="missing-circuit@example.com", hashed_password="not-used", first_name="Test", last_name="User", is_verified=True)
        workspace = Workspace(name="Missing circuit workspace", owner=user)
        session.add_all([user, workspace])
        await session.flush()
        with pytest.raises(CircuitStateUnavailableError, match="unavailable"):
            await WithdrawalService(session).create(request(), user)


@pytest.mark.asyncio
async def test_unauthorized_actor_cannot_change_circuit_for_arbitrary_workspace():
    async with sqlite_session() as session:
        actor = User(email="not-founder@example.com", hashed_password="not-used", first_name="Regular", last_name="User", is_verified=True)
        session.add(actor)
        await session.flush()
        with pytest.raises(OperationsError, match="Founder approval"):
            await WithdrawalService(session).change_circuit(uuid4(), WithdrawalCircuitState.PAUSED, "test", actor)


@pytest.mark.asyncio
@pytest.mark.parametrize("state", [WithdrawalCircuitState.PAUSED.value, WithdrawalCircuitState.EMERGENCY_STOP.value])
async def test_withdrawal_fails_closed_when_circuit_is_not_enabled(state):
    async with sqlite_session() as session:
        user = User(email="user@example.com", hashed_password="not-used", first_name="Test", last_name="User", is_verified=True)
        workspace = Workspace(name="User workspace", owner=user)
        session.add_all([user, workspace])
        await session.flush()
        session.add(WithdrawalCircuitBreaker(workspace_id=workspace.id, state=state))
        await session.flush()
        with pytest.raises(WithdrawalCircuitOpenError):
            await WithdrawalService(session).create(request(), user)


@pytest.mark.asyncio
async def test_withdrawal_lookup_rejects_cross_account_access():
    async with sqlite_session() as session:
        owner = User(email="withdrawal-owner@example.com", hashed_password="x", first_name="Test", last_name="User", is_verified=True)
        other = User(email="withdrawal-other@example.com", hashed_password="x", first_name="Other", last_name="User", is_verified=True)
        workspace = Workspace(name="Owner workspace", tenant=Tenant(name="Owner tenant"), owner=owner)
        session.add_all([owner, other, workspace])
        await session.flush()
        item = WithdrawalRequest(
            workspace_id=workspace.id, user_id=owner.id, amount=Decimal("1"), currency="ETH",
            destination_wallet_address="0x0000000000000000000000000000000000000001", network="Ethereum Mainnet",
            risk_acknowledged=True, idempotency_key="lookup-key", request_fingerprint="fingerprint",
            state=WithdrawalState.UNDER_REVIEW.value, notification_status="failed",
        )
        session.add(item)
        await session.flush()
        with pytest.raises(OperationsError, match="authorized"):
            await WithdrawalService(session).authorize_request_access(item, other)


@pytest.mark.asyncio
async def test_support_detail_fails_closed_for_ambiguous_customer_workspaces():
    async with sqlite_session() as session:
        customer = User(email="ambiguous-support@example.com", hashed_password="x", first_name="Test", last_name="User", is_verified=True)
        owner = User(email="workspace-owner@example.com", hashed_password="x", first_name="Workspace", last_name="Owner", is_verified=True)
        first = Workspace(name="First support workspace", owner=customer)
        second = Workspace(name="Second support workspace", owner=owner)
        session.add_all([customer, owner, first, second])
        await session.flush()
        session.add(WorkspaceMembership(workspace_id=second.id, user_id=customer.id))
        await session.commit()

        with pytest.raises(OperationsError, match="ambiguous workspace"):
            await CustomerSupportService(session).detail(customer.id, actor_id=owner.id)


@pytest.mark.asyncio
async def test_withdrawal_retries_postgres_deadlock_with_bounded_attempts(monkeypatch):
    class Deadlock:
        sqlstate = "40P01"

    async with sqlite_session() as session:
        service = WithdrawalService(session)
        calls = 0

        async def flaky_create(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls < 3:
                raise OperationalError("deadlock", {}, Deadlock())
            return "committed"

        monkeypatch.setattr(service, "_create_once", flaky_create)
        actor = User(email="retry@example.com", hashed_password="x", first_name="Test", last_name="User")
        session.add(actor)
        await session.flush()
        await session.commit()
        assert await service.create(request(), actor) == "committed"
        assert calls == 3


@pytest.mark.asyncio
async def test_withdrawal_does_not_retry_non_postgres_database_error(monkeypatch):
    class OtherDatabaseError:
        sqlstate = "23505"

    async with sqlite_session() as session:
        service = WithdrawalService(session)
        calls = 0

        async def failing_create(*args, **kwargs):
            nonlocal calls
            calls += 1
            raise OperationalError("constraint", {}, OtherDatabaseError())

        monkeypatch.setattr(service, "_create_once", failing_create)
        actor = User(email="no-retry@example.com", hashed_password="x", first_name="Test", last_name="User")
        session.add(actor)
        await session.flush()
        await session.commit()
        with pytest.raises(OperationalError):
            await service.create(request(), actor)
        assert calls == 1


def test_withdrawal_state_machine_does_not_allow_arbitrary_transitions():
    assert WithdrawalState.COMPLETED not in WithdrawalService.TRANSITIONS[WithdrawalState.UNDER_REVIEW]
    assert WithdrawalState.EXECUTING in WithdrawalService.TRANSITIONS[WithdrawalState.APPROVED]
    assert WithdrawalState.APPROVED not in WithdrawalService.TRANSITIONS[WithdrawalState.REQUESTED]


def test_withdrawal_schema_rejects_network_and_acknowledgement_mismatch():
    with pytest.raises(ValueError):
        request(network="Polygon")
    with pytest.raises(ValueError):
        request(confirmation=False)
