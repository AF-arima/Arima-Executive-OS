from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
import json
from typing import Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import TradeExecution, User, Workspace, WorkspaceMembership, WithdrawalCircuitBreaker, WithdrawalCircuitState
from app.quant.contracts import ResearchSignal
from app.services.audit import record_audit
from app.database.models import AuditAction, AuditEntity
from app.services.permissions import has_founder_control_access
from app.services.identity import DatabaseFinancialContextAuthorizer
from app.services.risk_contract import CircuitStateUnavailableError, FinancialContextAuthorizer, RiskDecision, RiskEngine, RiskExecutionContext, RiskLimits, RiskProvider, RiskValidationError


class QLabResearch(Protocol):
    async def research(self, *, workspace_id: UUID, query: str) -> object: ...


class QTradeExecution(Protocol):
    async def submit_order(self, *, workspace_id: UUID, account_id: UUID, asset: str, quantity: Decimal) -> object: ...


class ExecutionState(StrEnum):
    PROPOSED = "proposed"
    RISK_CHECK = "risk_check"
    APPROVED = "approved"
    BLOCKED = "blocked"
    SUBMITTED = "submitted"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


EXECUTION_TRANSITIONS: dict[ExecutionState, frozenset[ExecutionState]] = {
    ExecutionState.PROPOSED: frozenset({ExecutionState.RISK_CHECK, ExecutionState.BLOCKED}),
    ExecutionState.RISK_CHECK: frozenset({ExecutionState.APPROVED, ExecutionState.BLOCKED, ExecutionState.REJECTED}),
    ExecutionState.APPROVED: frozenset({ExecutionState.SUBMITTED, ExecutionState.CANCELLED, ExecutionState.BLOCKED}),
    ExecutionState.SUBMITTED: frozenset({ExecutionState.PARTIALLY_FILLED, ExecutionState.FILLED, ExecutionState.CANCELLED, ExecutionState.REJECTED}),
    ExecutionState.PARTIALLY_FILLED: frozenset({ExecutionState.FILLED, ExecutionState.CANCELLED}),
    ExecutionState.FILLED: frozenset(),
    ExecutionState.BLOCKED: frozenset(),
    ExecutionState.CANCELLED: frozenset(),
    ExecutionState.REJECTED: frozenset(),
}


def allowed_execution_transition(current: ExecutionState, target: ExecutionState) -> bool:
    return target in EXECUTION_TRANSITIONS[current]


def execution_request_fingerprint(signal: ResearchSignal, *, tenant_id: UUID, actor_id: UUID) -> str:
    payload = {
        "tenant_id": str(tenant_id), "workspace_id": str(signal.workspace_id),
        "account_id": str(signal.account_id), "actor_id": str(actor_id),
        "signal_id": str(signal.signal_id), "asset": signal.asset,
        "strategy": signal.strategy, "entry": str(signal.entry), "stop": str(signal.stop),
        "targets": [str(target) for target in signal.targets],
    }
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class ExecutionPermission:
    allowed: bool
    reason: str


class DisabledQTradeExecution:
    async def submit_order(self, *, workspace_id: UUID, account_id: UUID, asset: str, quantity: Decimal) -> object:
        del workspace_id, account_id, asset, quantity
        raise RuntimeError("QTRADE execution is not enabled")


class QTradeDryRunAdapter:
    """Pure deterministic proposal builder; it has no network or ledger access."""

    def propose(self, *, signal: ResearchSignal, decision: RiskDecision) -> dict[str, object]:
        return {
            "status": "DRY_RUN",
            "not_executed": True,
            "asset": signal.asset,
            "quantity": str(decision.position_size),
            "risk_amount": str(decision.risk_amount),
            "entry": str(signal.entry),
            "stop": str(signal.stop),
            "targets": [str(target) for target in signal.targets],
        }


class QTradeExecutionService:
    """Risk-gated QTrade boundary. It intentionally never submits live orders."""

    def __init__(self, session: AsyncSession, *, risk_provider: RiskProvider, limits: RiskLimits, context_authorizer: FinancialContextAuthorizer, execution_enabled: bool = False) -> None:
        self.session = session
        self.risk_provider = risk_provider
        self.limits = limits
        if not isinstance(context_authorizer, DatabaseFinancialContextAuthorizer):
            raise RiskValidationError("QTrade requires the concrete financial-context authorizer")
        self.context_authorizer = context_authorizer
        if execution_enabled:
            raise RuntimeError("QTRADE execution is not enabled in this release")
        self.execution_enabled = False
        self.dry_run = QTradeDryRunAdapter()
        self.risk_engine = RiskEngine()

    async def _circuit_state(self, workspace_id: UUID) -> WithdrawalCircuitState:
        row = await self.session.scalar(
            select(WithdrawalCircuitBreaker)
            .where(WithdrawalCircuitBreaker.workspace_id == workspace_id)
            .with_for_update()
        )
        if row is None:
            raise CircuitStateUnavailableError("Circuit breaker state is unavailable")
        return WithdrawalCircuitState(row.state)

    async def _authorize_execution_context(self, *, actor: User, signal: ResearchSignal) -> None:
        account = await self.session.get(User, signal.account_id)
        if account is None:
            raise RiskValidationError("Execution account is unavailable")
        workspace = await self.session.scalar(
            select(Workspace)
            .outerjoin(WorkspaceMembership, WorkspaceMembership.workspace_id == Workspace.id)
            .where(
                Workspace.id == signal.workspace_id,
                (Workspace.owner_id == account.id) | (WorkspaceMembership.user_id == account.id),
            )
        )
        if workspace is None:
            raise RiskValidationError("Execution account is not authorized for the workspace")
        if actor.id != account.id and not has_founder_control_access(actor):
            raise RiskValidationError("Actor is not authorized for the execution account")

    async def evaluate(self, *, tenant_id: UUID, actor: User, signal: ResearchSignal, idempotency_key: str) -> TradeExecution:
        if signal.tenant_id is None or signal.actor_id is None:
            raise RiskValidationError("Execution requires tenant and actor identity")
        await self._authorize_execution_context(actor=actor, signal=signal)
        await self.context_authorizer.authorize(
            tenant_id=tenant_id, workspace_id=signal.workspace_id,
            actor_id=actor.id, account_id=signal.account_id,
        )
        actor_id = actor.id
        context = RiskExecutionContext(
            tenant_id=tenant_id, workspace_id=signal.workspace_id,
            actor_id=actor_id, account_id=signal.account_id,
        )
        if context.tenant_id != signal.tenant_id or context.actor_id != signal.actor_id:
            raise RiskValidationError("Execution identity does not match the research signal")
        existing = await self.session.scalar(select(TradeExecution).where(
            TradeExecution.workspace_id == signal.workspace_id,
            TradeExecution.idempotency_key == idempotency_key,
        ))
        if existing is not None:
            fingerprint = execution_request_fingerprint(signal, tenant_id=tenant_id, actor_id=actor.id)
            if existing.request_fingerprint.startswith("legacy-unverified:"):
                raise RiskValidationError("Legacy execution request requires safe reauthorization")
            if existing.request_fingerprint != fingerprint:
                raise RiskValidationError("Execution idempotency key is bound to a different request")
            return existing
        fingerprint = execution_request_fingerprint(signal, tenant_id=tenant_id, actor_id=actor.id)
        record = TradeExecution(
            tenant_id=tenant_id, workspace_id=signal.workspace_id, actor_id=actor_id,
            account_id=signal.account_id, asset=signal.asset, strategy=signal.strategy,
            signal_id=signal.signal_id, provider=signal.provenance.provider,
            quantity=Decimal("0"), idempotency_key=idempotency_key,
            request_fingerprint=fingerprint,
            state=ExecutionState.PROPOSED.value, risk_decision="not_evaluated",
            circuit_state="unavailable", execution_permission=False,
            signal_provenance={"provider": signal.provenance.provider, "source": signal.provenance.source, "instrument": signal.provenance.instrument, "reference": signal.provenance.reference, "received_at": signal.provenance.received_at.isoformat()},
        )
        self.session.add(record)
        await self.session.flush()
        try:
            circuit = await self._circuit_state(signal.workspace_id)
        except CircuitStateUnavailableError as error:
            record.state = ExecutionState.BLOCKED.value
            record.rejection_reason = str(error)
            record.dry_run_result = {"status": "NOT_EXECUTED", "reason": str(error)}
            record_audit(self.session, actor_id=actor_id, action=AuditAction.STATUS_CHANGE, entity=AuditEntity.ACCOUNT, entity_id=record.id, event_type="QTRADE_EXECUTION_DECISION", event_metadata={"tenant_id": str(tenant_id), "workspace_id": str(signal.workspace_id), "account_id": str(signal.account_id), "state": record.state, "circuit_state": "unavailable", "execution_permission": False, "reason": str(error)})
            await self.session.commit()
            return record
        record.circuit_state = circuit.value
        reason: str | None = None
        decision: RiskDecision | None = None
        if circuit is not WithdrawalCircuitState.ENABLED:
            reason = f"execution circuit is {circuit.value}"
        else:
            record.state = ExecutionState.RISK_CHECK.value
            try:
                snapshot = await self.risk_provider.snapshot(
                    tenant_id=tenant_id, workspace_id=signal.workspace_id,
                    actor_id=actor_id, account_id=signal.account_id,
                )
                decision = self.risk_engine.validate(
                    signal=signal, snapshot=snapshot, limits=self.limits, context=context,
                )
                record.risk_decision = decision.reason
                record.quantity = decision.position_size
                record.dry_run_result = {"status": "NOT_EXECUTED", "risk_inputs": _risk_input_metadata(snapshot)}
                if not decision.allowed:
                    reason = decision.reason
            except Exception as error:
                if isinstance(error, (RiskValidationError, RuntimeError)):
                    reason = str(error)
                else:
                    raise
        if reason is None:
            reason = "QTRADE execution is disabled"
            if decision is not None and decision.allowed:
                record.dry_run_result = {
                    **self.dry_run.propose(signal=signal, decision=decision),
                    "risk_inputs": _risk_input_metadata(snapshot),
                }
        if reason is not None:
            record.state = ExecutionState.BLOCKED.value
            record.rejection_reason = reason
            record.execution_permission = False
        else:
            record.state = ExecutionState.APPROVED.value
            record.execution_permission = True
        record.risk_decision = record.risk_decision or "not_evaluated"
        record_audit(self.session, actor_id=actor_id, action=AuditAction.STATUS_CHANGE, entity=AuditEntity.ACCOUNT, entity_id=record.id, event_type="QTRADE_EXECUTION_DECISION", event_metadata={"tenant_id": str(tenant_id), "workspace_id": str(signal.workspace_id), "account_id": str(signal.account_id), "asset": signal.asset, "strategy": signal.strategy, "state": record.state, "risk_decision": record.risk_decision, "risk_inputs": (record.dry_run_result or {}).get("risk_inputs"), "circuit_state": circuit.value, "execution_permission": record.execution_permission, "reason": record.rejection_reason})
        await self.session.commit()
        return record


def _risk_input_metadata(snapshot) -> dict[str, object]:
    return {
        "tenant_id": str(snapshot.tenant_id) if snapshot.tenant_id else None,
        "workspace_id": str(snapshot.workspace_id),
        "actor_id": str(snapshot.actor_id) if snapshot.actor_id else None,
        "account_id": str(snapshot.account_id),
        "current_exposure": str(snapshot.current_exposure) if snapshot.current_exposure is not None else None,
        "concentration": {key: str(value) for key, value in (snapshot.concentration or {}).items()} if snapshot.concentration is not None else None,
        "realized_pnl": str(snapshot.realized_pnl) if snapshot.realized_pnl is not None else None,
        "unrealized_pnl": str(snapshot.unrealized_pnl) if snapshot.unrealized_pnl is not None else None,
        "daily_loss": str(snapshot.daily_loss) if snapshot.daily_loss is not None else None,
        "strategy_exposure": {key: str(value) for key, value in (snapshot.strategy_exposure or {}).items()} if snapshot.strategy_exposure is not None else None,
        "provenance": {
            "source": snapshot.provenance.source,
            "calculated_at": snapshot.provenance.calculated_at.isoformat(),
            "valuation_source": snapshot.provenance.valuation_source,
            "freshness_seconds": snapshot.provenance.freshness_seconds,
            "inputs": sorted(snapshot.provenance.input_names),
        } if snapshot.provenance is not None else None,
    }
