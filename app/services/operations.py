from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
import json
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.exceptions import EmailDeliveryError
from app.auth.security import SecurityRateLimiter
from app.core.config import Settings, get_settings
from app.database.models import (
    AuditAction,
    AuditEntity,
    SecurityEvent,
    RefreshTokenSession,
    User,
    WithdrawalCircuitBreaker,
    WithdrawalCircuitState,
    WithdrawalRequest,
    WithdrawalState,
    Workspace,
    WorkspaceMembership,
    LedgerBucket,
)
from app.email.service import TransactionalEmailService
from app.services.audit import record_audit
from app.services.permissions import has_founder_control_access
from app.services.ledger import LedgerError, LedgerService
from app.services.risk_contract import CircuitStateUnavailableError
from app.services.identity import FinancialContextError, FinancialContextResolver
from app.services.postgres_retry import is_retryable_postgres_error


class OperationsError(RuntimeError):
    pass


class BalanceUnavailableError(OperationsError):
    pass


class InsufficientBalanceError(OperationsError):
    pass


class WithdrawalCircuitOpenError(OperationsError):
    pass


class InvalidWithdrawalTransition(OperationsError):
    pass


class BalanceSource(Protocol):
    async def available_balance(self, *, user_id: UUID, workspace_id: UUID, currency: str) -> Decimal | None: ...


class UnconfiguredBalanceSource:
    async def available_balance(self, *, user_id: UUID, workspace_id: UUID, currency: str) -> Decimal | None:
        del user_id, workspace_id, currency
        return None


class LedgerBalanceSource:
    def __init__(self, session: AsyncSession) -> None:
        self.ledger = LedgerService(session)

    async def available_balance(self, *, user_id: UUID, workspace_id: UUID, currency: str) -> Decimal | None:
        account = await self.ledger.account(workspace_id=workspace_id, user_id=user_id, asset=currency)
        return (await self.ledger.balance(account_id=account.id, asset=currency)).available_balance


def mask_wallet(address: str) -> str:
    return address[:6] + "…" + address[-4:]


def withdrawal_request_fingerprint(data: Any, *, user_id: UUID, workspace_id: UUID) -> str:
    payload = {
        "user_id": str(user_id),
        "workspace_id": str(workspace_id),
        "amount": str(data.amount),
        "currency": str(data.currency).upper(),
        "destination_wallet_address": str(data.destination_wallet_address).lower(),
        "network": str(data.network).lower(),
        "risk_acknowledgement": bool(data.risk_acknowledgement),
        "first_name": str(data.first_name).casefold(),
        "last_name": str(data.last_name).casefold(),
    }
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _workspace_query(user_id: UUID):
    return select(Workspace).outerjoin(WorkspaceMembership, WorkspaceMembership.workspace_id == Workspace.id).where(
        or_(Workspace.owner_id == user_id, WorkspaceMembership.user_id == user_id)
    )


class CustomerSupportService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def search(self, query: str, *, actor_id: UUID, workspace_id: UUID | None = None, limit: int = 50) -> list[User]:
        value = query.strip()
        if not value:
            return []
        conditions = [
            func.lower(User.email).like(f"%{value.lower()}%"),
            func.lower(User.first_name).like(f"%{value.lower()}%"),
            func.lower(User.last_name).like(f"%{value.lower()}%"),
        ]
        try:
            conditions.append(User.id == UUID(value))
        except ValueError:
            pass
        statement = select(User).where(or_(*conditions)).order_by(User.created_at.desc()).limit(limit)
        if workspace_id is not None:
            statement = statement.join(WorkspaceMembership, WorkspaceMembership.user_id == User.id).where(WorkspaceMembership.workspace_id == workspace_id)
        result = list((await self.session.scalars(statement)).all())
        record_audit(self.session, actor_id=actor_id, action=AuditAction.READ, entity=AuditEntity.ACCOUNT, entity_id=actor_id, event_type="ACCOUNT_LOOKUP")
        await self.session.commit()
        return result

    async def detail(self, user_id: UUID, *, actor_id: UUID) -> tuple[User, list[SecurityEvent], list[Any], UUID | None]:
        user = await self.session.get(User, user_id)
        if user is None:
            raise OperationsError("Customer account not found")
        workspaces = list((await self.session.scalars(_workspace_query(user_id).order_by(Workspace.created_at))).all())
        if len(workspaces) > 1:
            raise OperationsError("Customer account has ambiguous workspace scope")
        workspace = workspaces[0] if workspaces else None
        events = list((await self.session.scalars(select(SecurityEvent).where(SecurityEvent.user_id == user_id).order_by(SecurityEvent.occurred_at.desc()).limit(50))).all())
        sessions = list((await self.session.scalars(select(RefreshTokenSession).where(RefreshTokenSession.user_id == user_id).order_by(RefreshTokenSession.created_at.desc()).limit(50))).all())
        record_audit(self.session, actor_id=actor_id, action=AuditAction.READ, entity=AuditEntity.ACCOUNT, entity_id=user_id, event_type="ACCOUNT_VIEW")
        await self.session.commit()
        return user, events, sessions, workspace.id if workspace else None


class WithdrawalService:
    TRANSITIONS: dict[WithdrawalState, frozenset[WithdrawalState]] = {
        WithdrawalState.REQUESTED: frozenset({WithdrawalState.UNDER_REVIEW, WithdrawalState.CANCELLED, WithdrawalState.BLOCKED}),
        WithdrawalState.UNDER_REVIEW: frozenset({WithdrawalState.APPROVED, WithdrawalState.REJECTED, WithdrawalState.CANCELLED, WithdrawalState.BLOCKED}),
        WithdrawalState.APPROVED: frozenset({WithdrawalState.EXECUTING, WithdrawalState.CANCELLED, WithdrawalState.BLOCKED}),
        WithdrawalState.EXECUTING: frozenset({WithdrawalState.COMPLETED, WithdrawalState.FAILED}),
        WithdrawalState.COMPLETED: frozenset(),
        WithdrawalState.REJECTED: frozenset(),
        WithdrawalState.CANCELLED: frozenset(),
        WithdrawalState.FAILED: frozenset(),
        WithdrawalState.BLOCKED: frozenset({WithdrawalState.UNDER_REVIEW, WithdrawalState.CANCELLED}),
    }

    def __init__(self, session: AsyncSession, *, settings: Settings | None = None, balance_source: BalanceSource | None = None, email_service: TransactionalEmailService | None = None) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self.balance_source = balance_source or LedgerBalanceSource(session)
        self.email_service = email_service

    async def workspace_for(self, user: User) -> Workspace:
        workspaces = list((await self.session.scalars(_workspace_query(user.id).order_by(Workspace.created_at))).all())
        if not workspaces:
            raise OperationsError("Authorized workspace is unavailable")
        if len(workspaces) != 1:
            raise OperationsError("Authorized workspace selection is ambiguous")
        return workspaces[0]

    async def authorize_request_access(self, request: WithdrawalRequest, actor: User) -> None:
        if request.user_id != actor.id and not has_founder_control_access(actor):
            raise OperationsError("Actor is not authorized for this withdrawal workspace")
        try:
            await FinancialContextResolver(self.session).resolve(
                actor=actor, workspace_id=request.workspace_id, account_id=request.user_id,
            )
        except FinancialContextError as error:
            raise OperationsError("Actor is not authorized for this withdrawal workspace") from error

    async def _circuit(self, workspace_id: UUID, *, initialize: bool = False, initial_state: WithdrawalCircuitState | None = None) -> WithdrawalCircuitBreaker:
        row = await self.session.scalar(
            select(WithdrawalCircuitBreaker)
            .where(WithdrawalCircuitBreaker.workspace_id == workspace_id)
            .with_for_update()
        )
        if row is None and initialize:
            if initial_state is None:
                raise CircuitStateUnavailableError("Circuit state initialization requires an explicit state")
            row = WithdrawalCircuitBreaker(workspace_id=workspace_id, state=initial_state.value, changed_at=datetime.now(UTC))
            self.session.add(row)
            await self.session.flush()
        if row is None:
            raise CircuitStateUnavailableError("Circuit breaker state is unavailable")
        return row

    async def create(
        self,
        data: Any,
        user: User,
        *,
        context: Any = None,
        require_authoritative_context: bool = False,
        _retry_attempt: int = 0,
    ) -> WithdrawalRequest:
        try:
            return await self._create_once(
                data,
                user,
                context=context,
                require_authoritative_context=require_authoritative_context,
            )
        except DBAPIError as error:
            actor_id = user.id
            await self.session.rollback()
            if not is_retryable_postgres_error(error) or _retry_attempt >= 2:
                raise
            await asyncio.sleep(0.05 * (2**_retry_attempt))
            retry_user = await self.session.get(User, actor_id)
            if retry_user is None:
                raise OperationsError("Authenticated withdrawal account is unavailable") from error
            return await self.create(
                data,
                retry_user,
                context=context,
                require_authoritative_context=require_authoritative_context,
                _retry_attempt=_retry_attempt + 1,
            )

    async def _create_once(self, data: Any, user: User, *, context: Any = None, require_authoritative_context: bool = False) -> WithdrawalRequest:
        await SecurityRateLimiter(self.session, self.settings).enforce(scope="withdrawal_request", key=str(user.id), limit=5, window=timedelta(hours=1))
        if data.first_name.casefold() != user.first_name.casefold() or data.last_name.casefold() != user.last_name.casefold():
            raise OperationsError("Withdrawal identity does not match the authenticated account")
        workspace = await self.workspace_for(user)
        if require_authoritative_context:
            try:
                await FinancialContextResolver(self.session).resolve(actor=user, workspace_id=workspace.id, account_id=user.id)
            except FinancialContextError as error:
                raise OperationsError(str(error)) from error
        circuit = await self._circuit(workspace.id)
        if WithdrawalCircuitState(circuit.state) is not WithdrawalCircuitState.ENABLED:
            raise WithdrawalCircuitOpenError("Withdrawals are temporarily unavailable")
        fingerprint = withdrawal_request_fingerprint(data, user_id=user.id, workspace_id=workspace.id)
        existing = await self.session.scalar(select(WithdrawalRequest).where(WithdrawalRequest.idempotency_key == data.idempotency_key))
        if existing is not None:
            if existing.user_id != user.id or existing.workspace_id != workspace.id:
                raise OperationsError("Withdrawal idempotency key is already bound to another account")
            if existing.request_fingerprint.startswith("legacy-unverified:"):
                raise OperationsError("Legacy withdrawal request requires safe reauthorization")
            if existing.request_fingerprint != fingerprint:
                raise OperationsError("Withdrawal idempotency key is bound to a different request payload")
            return existing
        balance = await self.balance_source.available_balance(user_id=user.id, workspace_id=workspace.id, currency=data.currency)
        if balance is None:
            raise BalanceUnavailableError("Available balance source is not configured")
        if data.amount > balance:
            raise InsufficientBalanceError("Insufficient available balance")
        try:
            # Keep the reservation and request creation in one savepoint.  If
            # the request insert loses a uniqueness race, the reservation is
            # rolled back with it rather than leaving funds stranded.
            async with self.session.begin_nested():
                await LedgerService(self.session).transfer_bucket(
                    workspace_id=workspace.id, user_id=user.id, asset=data.currency,
                    amount=data.amount, source_bucket=LedgerBucket.AVAILABLE,
                    target_bucket=LedgerBucket.RESERVED, transaction_type="withdrawal_reservation",
                    idempotency_key=f"withdrawal-reservation:{data.idempotency_key}", actor_id=user.id,
                    reference=str(data.idempotency_key),
                )
                request = WithdrawalRequest(
                    workspace_id=workspace.id, user_id=user.id, amount=data.amount, currency=data.currency,
                    destination_wallet_address=data.destination_wallet_address, network=data.network,
                    risk_acknowledged=data.risk_acknowledgement, idempotency_key=data.idempotency_key,
                    request_fingerprint=fingerprint,
                    state=WithdrawalState.UNDER_REVIEW.value, state_reason="Validated and queued for review",
                    notification_status="pending",
                )
                self.session.add(request)
                await self.session.flush()
                record_audit(self.session, actor_id=user.id, action=AuditAction.CREATE, entity=AuditEntity.WITHDRAWAL, entity_id=request.id, event_type="WITHDRAWAL_REQUESTED")
                record_audit(self.session, actor_id=user.id, action=AuditAction.STATUS_CHANGE, entity=AuditEntity.WITHDRAWAL, entity_id=request.id, event_type="WITHDRAWAL_REVIEWED")
        except LedgerError as error:
            raise InsufficientBalanceError("Insufficient available balance") from error
        except IntegrityError as error:
            existing = await self.session.scalar(select(WithdrawalRequest).where(
                WithdrawalRequest.idempotency_key == data.idempotency_key,
                WithdrawalRequest.user_id == user.id,
                WithdrawalRequest.workspace_id == workspace.id,
            ))
            if existing is not None:
                if existing.request_fingerprint.startswith("legacy-unverified:"):
                    raise OperationsError("Legacy withdrawal request requires safe reauthorization") from error
                if existing.request_fingerprint != fingerprint:
                    raise OperationsError("Withdrawal idempotency key is bound to a different request payload") from error
                return existing
            raise OperationsError("Withdrawal request could not be created safely") from error
        await self._notify(request, user)
        await self.session.commit()
        return request

    async def _notify(self, request: WithdrawalRequest, user: User) -> None:
        if self.email_service is None:
            request.notification_status = "failed"
            request.notification_error = "transactional_email_unavailable"
            record_audit(self.session, actor_id=request.user_id, action=AuditAction.CREATE, entity=AuditEntity.WITHDRAWAL, entity_id=request.id, event_type="EMAIL_NOTIFICATION_FAILED", event_metadata={"reason": "transactional_email_unavailable"})
            return
        try:
            await self.email_service.send_withdrawal_received(
                email=user.email, recipient_name=f"{user.first_name} {user.last_name}",
                request_id=request.id, amount=request.amount, currency=request.currency,
                masked_wallet=mask_wallet(request.destination_wallet_address), network=request.network,
            )
            request.notification_status = "sent"
            record_audit(self.session, actor_id=request.user_id, action=AuditAction.CREATE, entity=AuditEntity.WITHDRAWAL, entity_id=request.id, event_type="EMAIL_NOTIFICATION_SENT")
        except EmailDeliveryError:
            request.notification_status = "failed"
            request.notification_error = "delivery_failed"
            record_audit(self.session, actor_id=request.user_id, action=AuditAction.CREATE, entity=AuditEntity.WITHDRAWAL, entity_id=request.id, event_type="EMAIL_NOTIFICATION_FAILED", event_metadata={"reason": "delivery_failed"})

    async def transition(self, request_id: UUID, target: WithdrawalState, actor: User, *, reason: str, require_authoritative_context: bool = False) -> WithdrawalRequest:
        request = await self.session.scalar(
            select(WithdrawalRequest).where(WithdrawalRequest.id == request_id).with_for_update()
        )
        if request is None:
            raise OperationsError("Withdrawal request not found")
        workspace = await self.session.get(Workspace, request.workspace_id)
        if workspace is None:
            raise OperationsError("Withdrawal workspace is unavailable")
        if require_authoritative_context:
            try:
                await FinancialContextResolver(self.session).resolve(actor=actor, workspace_id=workspace.id, account_id=request.user_id)
            except FinancialContextError as error:
                raise OperationsError(str(error)) from error
        if actor.id != request.user_id and not has_founder_control_access(actor):
            raise OperationsError("Actor is not authorized for this withdrawal workspace")
        if actor.id == request.user_id:
            authorized_workspace = await self.session.scalar(_workspace_query(actor.id).where(Workspace.id == request.workspace_id))
            if authorized_workspace is None:
                raise OperationsError("Actor is not authorized for this withdrawal workspace")
        current = WithdrawalState(request.state)
        if target not in self.TRANSITIONS[current]:
            raise InvalidWithdrawalTransition(f"Cannot transition from {current.value} to {target.value}")
        if target in {WithdrawalState.APPROVED, WithdrawalState.REJECTED, WithdrawalState.BLOCKED} and not has_founder_control_access(actor):
            raise OperationsError("Founder approval is required")
        if target is WithdrawalState.APPROVED:
            circuit = await self._circuit(request.workspace_id)
            if WithdrawalCircuitState(circuit.state) is not WithdrawalCircuitState.ENABLED:
                raise WithdrawalCircuitOpenError("Withdrawals are temporarily unavailable")
        now = datetime.now(UTC)
        request.state = target.value
        request.state_reason = reason
        if target in {WithdrawalState.UNDER_REVIEW, WithdrawalState.REJECTED, WithdrawalState.CANCELLED}:
            request.reviewed_by_id = actor.id
            request.reviewed_at = now
        if target is WithdrawalState.APPROVED:
            request.approved_by_id = actor.id
            request.approved_at = now
        if target in {WithdrawalState.REJECTED, WithdrawalState.CANCELLED, WithdrawalState.FAILED}:
            try:
                await LedgerService(self.session).transfer_bucket(
                    workspace_id=request.workspace_id, user_id=request.user_id,
                    asset=request.currency, amount=request.amount,
                    source_bucket=LedgerBucket.RESERVED, target_bucket=LedgerBucket.AVAILABLE,
                    transaction_type="withdrawal_reservation_release",
                    idempotency_key=f"withdrawal-release:{request.id}:{target.value}",
                    actor_id=actor.id, reference=str(request.id),
                )
            except LedgerError as error:
                raise OperationsError("Reserved withdrawal funds could not be released") from error
        event = {
            WithdrawalState.REJECTED: "WITHDRAWAL_REJECTED", WithdrawalState.CANCELLED: "WITHDRAWAL_CANCELLED",
            WithdrawalState.APPROVED: "WITHDRAWAL_APPROVED", WithdrawalState.UNDER_REVIEW: "WITHDRAWAL_REVIEWED",
        }.get(target, "WITHDRAWAL_STATUS_CHANGED")
        record_audit(self.session, actor_id=actor.id, action=AuditAction.STATUS_CHANGE, entity=AuditEntity.WITHDRAWAL, entity_id=request.id, event_type=event)
        await self.session.commit()
        return request

    async def change_circuit(self, workspace_id: UUID, state: WithdrawalCircuitState, reason: str, actor: User, *, require_authoritative_context: bool = False) -> WithdrawalCircuitBreaker:
        if not has_founder_control_access(actor):
            raise OperationsError("Founder approval is required")
        workspace = await self.session.get(Workspace, workspace_id)
        if workspace is None:
            raise OperationsError("Circuit workspace is unavailable")
        if require_authoritative_context:
            try:
                await FinancialContextResolver(self.session).resolve(actor=actor, workspace_id=workspace_id, account_id=workspace.owner_id)
            except FinancialContextError as error:
                raise OperationsError(str(error)) from error
        existing = await self.session.scalar(
            select(WithdrawalCircuitBreaker)
            .where(WithdrawalCircuitBreaker.workspace_id == workspace_id)
            .with_for_update()
        )
        if existing is None:
            row = await self._circuit(workspace_id, initialize=True, initial_state=state)
            previous = "unavailable"
        else:
            row = existing
            previous = row.state
        row.state = state.value
        row.reason = reason
        row.changed_by_id = actor.id
        row.changed_at = datetime.now(UTC)
        record_audit(self.session, actor_id=actor.id, action=AuditAction.STATUS_CHANGE, entity=AuditEntity.WITHDRAWAL_CIRCUIT_BREAKER, entity_id=row.id, event_type="WITHDRAWAL_CIRCUIT_BREAKER_CHANGED", event_metadata={"previous_state": previous, "new_state": state.value, "reason": reason, "workspace_id": str(workspace_id)})
        await self.session.commit()
        return row
