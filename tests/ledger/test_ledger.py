from decimal import Decimal

import pytest

from app.database.models import LedgerBucket, LedgerDirection, User, Workspace
from app.services.ledger import LedgerError, LedgerImbalanceError, LedgerLine, LedgerReversalError, LedgerService
from tests.database.helpers import sqlite_session


@pytest.mark.asyncio
async def test_ledger_posts_double_entry_and_calculates_available_reserved_pending():
    async with sqlite_session() as session:
        owner = User(email="owner@example.com", hashed_password="x", first_name="Owner", last_name="User", is_verified=True)
        clearing = User(email="clearing@example.com", hashed_password="x", first_name="Clearing", last_name="User", is_verified=True)
        workspace = Workspace(name="Ledger workspace", owner=owner)
        session.add_all([owner, clearing, workspace])
        await session.flush()
        ledger = LedgerService(session)
        customer = await ledger.account(workspace_id=workspace.id, user_id=owner.id, asset="ETH")
        contra = await ledger.account(workspace_id=workspace.id, user_id=clearing.id, asset="ETH")
        await ledger.post(workspace_id=workspace.id, user_id=owner.id, actor_id=owner.id, transaction_type="deposit", idempotency_key="deposit-1", source="test", lines=(
            LedgerLine(contra.id, "ETH", LedgerDirection.DEBIT, LedgerBucket.AVAILABLE, Decimal("10.125")),
            LedgerLine(customer.id, "ETH", LedgerDirection.CREDIT, LedgerBucket.AVAILABLE, Decimal("10.125")),
        ))
        await ledger.transfer_bucket(workspace_id=workspace.id, user_id=owner.id, asset="ETH", amount=Decimal("2.25"), source_bucket=LedgerBucket.AVAILABLE, target_bucket=LedgerBucket.RESERVED, transaction_type="reserve", idempotency_key="reserve-1", actor_id=owner.id, reference="withdrawal")
        snapshot = await ledger.balance(account_id=customer.id, asset="ETH")
        assert snapshot.authoritative_balance == Decimal("10.125")
        assert snapshot.reserved_balance == Decimal("2.25")
        assert snapshot.available_balance == Decimal("7.875")


@pytest.mark.asyncio
async def test_ledger_rejects_imbalanced_transaction():
    async with sqlite_session() as session:
        owner = User(email="owner@example.com", hashed_password="x", first_name="Owner", last_name="User", is_verified=True)
        workspace = Workspace(name="Ledger workspace", owner=owner)
        session.add_all([owner, workspace])
        await session.flush()
        account = await LedgerService(session).account(workspace_id=workspace.id, user_id=owner.id, asset="ETH")
        with pytest.raises(LedgerImbalanceError):
            await LedgerService(session).post(workspace_id=workspace.id, user_id=owner.id, actor_id=owner.id, transaction_type="invalid", idempotency_key="invalid-1", source="test", lines=(LedgerLine(account.id, "ETH", LedgerDirection.CREDIT, LedgerBucket.AVAILABLE, Decimal("1")),))


@pytest.mark.asyncio
async def test_posted_ledger_history_is_reversed_by_compensating_entries():
    async with sqlite_session() as session:
        owner = User(email="owner@example.com", hashed_password="x", first_name="Owner", last_name="User", is_verified=True)
        contra = User(email="contra@example.com", hashed_password="x", first_name="Contra", last_name="User", is_verified=True)
        workspace = Workspace(name="Ledger workspace", owner=owner)
        session.add_all([owner, contra, workspace])
        await session.flush()
        ledger = LedgerService(session)
        customer = await ledger.account(workspace_id=workspace.id, user_id=owner.id, asset="ETH")
        other = await ledger.account(workspace_id=workspace.id, user_id=contra.id, asset="ETH")
        original = await ledger.post(workspace_id=workspace.id, user_id=owner.id, actor_id=owner.id, transaction_type="deposit", idempotency_key="deposit-reverse", source="test", lines=(LedgerLine(other.id, "ETH", LedgerDirection.DEBIT, LedgerBucket.AVAILABLE, Decimal("1")), LedgerLine(customer.id, "ETH", LedgerDirection.CREDIT, LedgerBucket.AVAILABLE, Decimal("1"))))
        await ledger.reverse(transaction_id=original.id, actor_id=owner.id, idempotency_key="reverse-1", reason="correction")
        snapshot = await ledger.balance(account_id=customer.id, asset="ETH")
        assert snapshot.authoritative_balance == Decimal("0")
        with pytest.raises(LedgerReversalError):
            await ledger.reverse(transaction_id=original.id, actor_id=owner.id, idempotency_key="reverse-2", reason="duplicate")


@pytest.mark.asyncio
async def test_ledger_idempotency_returns_the_original_transaction_without_duplicate_effects():
    async with sqlite_session() as session:
        owner = User(email="owner-idempotent@example.com", hashed_password="x", first_name="Owner", last_name="User", is_verified=True)
        contra = User(email="contra-idempotent@example.com", hashed_password="x", first_name="Contra", last_name="User", is_verified=True)
        workspace = Workspace(name="Idempotency workspace", owner=owner)
        session.add_all([owner, contra, workspace])
        await session.flush()
        ledger = LedgerService(session)
        customer = await ledger.account(workspace_id=workspace.id, user_id=owner.id, asset="ETH")
        other = await ledger.account(workspace_id=workspace.id, user_id=contra.id, asset="ETH")
        lines = (
            LedgerLine(other.id, "ETH", LedgerDirection.DEBIT, LedgerBucket.AVAILABLE, Decimal("1")),
            LedgerLine(customer.id, "ETH", LedgerDirection.CREDIT, LedgerBucket.AVAILABLE, Decimal("1")),
        )
        first = await ledger.post(workspace_id=workspace.id, user_id=owner.id, actor_id=owner.id, transaction_type="deposit", idempotency_key="same-key", source="test", lines=lines)
        second = await ledger.post(workspace_id=workspace.id, user_id=owner.id, actor_id=owner.id, transaction_type="deposit", idempotency_key="same-key", source="test", lines=lines)
        assert second.id == first.id
        assert (await ledger.balance(account_id=customer.id, asset="ETH")).authoritative_balance == Decimal("1")


@pytest.mark.asyncio
async def test_ledger_idempotency_rejects_same_key_with_different_payload():
    async with sqlite_session() as session:
        owner = User(email="owner-ledger-conflict@example.com", hashed_password="x", first_name="Owner", last_name="User", is_verified=True)
        contra = User(email="contra-ledger-conflict@example.com", hashed_password="x", first_name="Contra", last_name="User", is_verified=True)
        workspace = Workspace(name="Ledger conflict workspace", owner=owner)
        session.add_all([owner, contra, workspace])
        await session.flush()
        ledger = LedgerService(session)
        customer = await ledger.account(workspace_id=workspace.id, user_id=owner.id, asset="ETH")
        clearing = await ledger.account(workspace_id=workspace.id, user_id=contra.id, asset="ETH")
        lines = (
            LedgerLine(clearing.id, "ETH", LedgerDirection.DEBIT, LedgerBucket.AVAILABLE, Decimal("1")),
            LedgerLine(customer.id, "ETH", LedgerDirection.CREDIT, LedgerBucket.AVAILABLE, Decimal("1")),
        )
        await ledger.post(workspace_id=workspace.id, user_id=owner.id, actor_id=owner.id, transaction_type="deposit", idempotency_key="ledger-conflict", source="test", lines=lines)
        changed = (
            LedgerLine(clearing.id, "ETH", LedgerDirection.DEBIT, LedgerBucket.AVAILABLE, Decimal("2")),
            LedgerLine(customer.id, "ETH", LedgerDirection.CREDIT, LedgerBucket.AVAILABLE, Decimal("2")),
        )
        with pytest.raises(LedgerError, match="different request payload"):
            await ledger.post(workspace_id=workspace.id, user_id=owner.id, actor_id=owner.id, transaction_type="deposit", idempotency_key="ledger-conflict", source="test", lines=changed)
