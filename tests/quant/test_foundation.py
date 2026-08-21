from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from app.quant.contracts import MarketRegime, QLabService, QLabUnavailableError, ResearchProvenance, ResearchSignal
from app.quant.evidence import OHLCVBar, build_structural_evidence
from app.quant.market_adapter import MarketObservation, StrategyEvidence, normalize_strategy_evidence
from app.quant.strategy_evidence import StrategyEvidenceProvenance
from app.database.models import User
from app.services.identity import DatabaseFinancialContextAuthorizer
from app.services.risk_contract import AuthoritativeRiskInputs, RiskEngine, RiskExecutionContext, RiskLimits, RiskProvenance, RiskValidationError, TrustedRiskSnapshotBuilder, _issue_risk_authority
from app.services.trading_contracts import ExecutionState, QTradeExecutionService, allowed_execution_transition


def signal(*, workspace_id=None, account_id=None) -> ResearchSignal:
    now = datetime.now(UTC)
    tenant_id = uuid4()
    actor_id = uuid4()
    return ResearchSignal(
        signal_id=uuid4(), tenant_id=tenant_id, workspace_id=workspace_id or uuid4(), actor_id=actor_id, account_id=account_id or actor_id,
        asset="BTCUSD", strategy="trend", timeframe="1h", regime=MarketRegime.TRENDING, direction="long",
        confidence=Decimal("0.8"), entry=Decimal("100"), stop=Decimal("90"),
        targets=(Decimal("120"),), expected_risk_reward=Decimal("2"),
        rationale="verified provider signal", timestamp=now,
        provenance=ResearchProvenance("provider", "source", "BTCUSD", now, now, "run-1"),
    )


def evidence(**overrides) -> StrategyEvidence:
    values = dict(
        direction="long", entry=Decimal("100"), stop=Decimal("90"), targets=(Decimal("120"),),
        confidence=Decimal("0.8"), expected_risk_reward=Decimal("2"), strategy="trend",
        timeframe="1h", regime=MarketRegime.TRENDING, rationale="verified structure",
        liquidity_sweep_confirmed=True, mss_confirmed=True, pullback_confirmed=True,
        news_clear=True, session_allowed=True,
        provenance=StrategyEvidenceProvenance("market", "structure", "session", "news", datetime.now(UTC)),
    )
    values.update(overrides)
    return StrategyEvidence(**values)


def bars(count=20):
    start = datetime.now(UTC) - timedelta(minutes=count)
    values = [OHLCVBar("BTC/USD", "1m", start + timedelta(minutes=index), Decimal("100"), Decimal("101"), Decimal("99"), Decimal("100")) for index in range(count - 1)]
    values.append(OHLCVBar("BTC/USD", "1m", start + timedelta(minutes=count - 1), Decimal("100"), Decimal("103"), Decimal("98"), Decimal("102")))
    return tuple(values)


def trusted_snapshot(item, *, account_id=None, **overrides):
    values = dict(
        tenant_id=item.tenant_id, workspace_id=item.workspace_id, account_id=account_id or item.account_id,
        actor_id=item.actor_id, total_equity=Decimal("1000"), available_capital=Decimal("500"),
        reserved_capital=Decimal("0"), current_exposure=Decimal("0"), asset_exposure={"BTCUSD": Decimal("0")},
        concentration={"BTCUSD": Decimal("0")}, realized_pnl=Decimal("0"), unrealized_pnl=Decimal("0"),
        daily_loss=Decimal("0"), strategy_exposure={"trend": Decimal("0")},
        provenance=RiskProvenance("test", datetime.now(UTC), RiskEngine.REQUIRED_INPUTS, valuation_source="ledger-fixture"),
    )
    values.update(overrides)
    return TrustedRiskSnapshotBuilder(_issue_risk_authority()).build(AuthoritativeRiskInputs(**values))


def test_research_signal_requires_provenance_and_timestamp() -> None:
    item = signal()
    assert item.provenance.reference == "run-1"
    with pytest.raises(ValueError, match="timezone-aware"):
        ResearchProvenance("p", "s", "i", datetime.now(), datetime.now(), "r")


@pytest.mark.asyncio
async def test_qlab_fails_closed_without_verified_provider() -> None:
    with pytest.raises(QLabUnavailableError, match="No verified"):
        await QLabService().research(
            tenant_id=uuid4(), workspace_id=uuid4(), actor_id=uuid4(), account_id=uuid4(), user=object(), run_id=uuid4(), asset="BTCUSD", strategy="trend",
        )


def test_strategy_normalization_rejects_rr_and_missing_confirmation() -> None:
    now = datetime.now(UTC)
    observation = MarketObservation("provider", "BTC/USD", Decimal("100"), now, "ref")
    with pytest.raises(QLabUnavailableError, match="risk/reward"):
        normalize_strategy_evidence(observation=observation, tenant_id=uuid4(), workspace_id=uuid4(), actor_id=uuid4(), account_id=uuid4(), asset="BTCUSD", evidence=evidence(expected_risk_reward=Decimal("1.5")))
    with pytest.raises(QLabUnavailableError, match="confirmations"):
        normalize_strategy_evidence(observation=observation, tenant_id=uuid4(), workspace_id=uuid4(), actor_id=uuid4(), account_id=uuid4(), asset="BTCUSD", evidence=evidence(mss_confirmed=False))


def test_market_observation_rejects_unverified_or_malformed_data() -> None:
    now = datetime.now(UTC)
    with pytest.raises(ValueError, match="positive"):
        MarketObservation("provider", "BTC/USD", Decimal("0"), now, "ref")
    with pytest.raises(ValueError, match="verified fresh"):
        MarketObservation("provider", "BTC/USD", Decimal("100"), now, "ref", status="STALE")


def test_structural_evidence_is_deterministic_and_detects_sweep_mss_retest() -> None:
    candle_set = bars()
    generated = candle_set[-1].observed_at + timedelta(seconds=1)
    first = build_structural_evidence(bars=candle_set, provider="provider", source="source", provenance="run-1", generated_at=generated, max_age_seconds=60)
    second = build_structural_evidence(bars=candle_set, provider="provider", source="source", provenance="run-1", generated_at=generated, max_age_seconds=60)
    assert first == second
    assert first.liquidity_sweep and first.market_structure_shift and first.pullback_retest


def test_structural_evidence_rejects_insufficient_and_stale_candles() -> None:
    candle_set = bars(19)
    generated = candle_set[-1].observed_at + timedelta(seconds=1)
    with pytest.raises(QLabUnavailableError, match="EVIDENCE_INSUFFICIENT"):
        build_structural_evidence(bars=candle_set, provider="provider", source="source", provenance="run-1", generated_at=generated, max_age_seconds=60)
    with pytest.raises(QLabUnavailableError, match="stale"):
        build_structural_evidence(bars=bars(), provider="provider", source="source", provenance="run-1", generated_at=generated + timedelta(hours=1), max_age_seconds=60)


def test_risk_engine_sizes_only_from_authoritative_snapshot() -> None:
    item = signal()
    snapshot = trusted_snapshot(item)
    decision = RiskEngine().validate(
        signal=item,
        snapshot=snapshot,
        limits=RiskLimits(maximum_risk=Decimal("50")),
        context=RiskExecutionContext(item.tenant_id, item.workspace_id, item.actor_id, item.account_id),
    )
    assert decision.allowed is True
    assert decision.position_size == Decimal("5.00000000")


def test_risk_engine_rejects_cross_account_context() -> None:
    item = signal()
    with pytest.raises(RiskValidationError, match="does not match"):
        RiskEngine().validate(
            signal=item,
            snapshot=trusted_snapshot(item, account_id=uuid4()),
            limits=RiskLimits(maximum_risk=Decimal("50")),
            context=RiskExecutionContext(item.tenant_id, item.workspace_id, item.actor_id, item.account_id),
        )


def test_execution_state_machine_has_no_direct_fill_or_block_bypass() -> None:
    assert allowed_execution_transition(ExecutionState.PROPOSED, ExecutionState.RISK_CHECK)
    assert not allowed_execution_transition(ExecutionState.PROPOSED, ExecutionState.FILLED)
    assert not allowed_execution_transition(ExecutionState.BLOCKED, ExecutionState.SUBMITTED)


class FakeRisk:
    async def snapshot(self, **kwargs):
        return TrustedRiskSnapshotBuilder(_issue_risk_authority()).build(AuthoritativeRiskInputs(
            tenant_id=kwargs["tenant_id"], workspace_id=kwargs["workspace_id"], account_id=kwargs["account_id"],
            actor_id=kwargs["actor_id"], total_equity=Decimal("1000"), available_capital=Decimal("500"),
            reserved_capital=Decimal("0"), current_exposure=Decimal("0"), asset_exposure={"BTCUSD": Decimal("0")}, concentration={"BTCUSD": Decimal("0")},
            realized_pnl=Decimal("0"), unrealized_pnl=Decimal("0"), daily_loss=Decimal("0"), strategy_exposure={"trend": Decimal("0")},
            provenance=RiskProvenance("test", datetime.now(UTC), RiskEngine.REQUIRED_INPUTS, valuation_source="ledger-fixture"),
        ))


class AllowContext(DatabaseFinancialContextAuthorizer):
    def __init__(self):
        pass

    async def authorize(self, **kwargs):
        del kwargs


class UntrustedContext:
    async def authorize(self, **kwargs):
        del kwargs


def test_qtrade_rejects_non_database_authorizer() -> None:
    with pytest.raises(RiskValidationError, match="concrete financial-context authorizer"):
        QTradeExecutionService(
            FakeSession(), risk_provider=FakeRisk(), limits=RiskLimits(Decimal("1")),
            context_authorizer=UntrustedContext(), execution_enabled=False,
        )


class FakeSession:
    def __init__(self, actor=None, workspace_id=None, circuit_enabled=True):
        self.items = []
        self.actor = actor
        self.workspace_id = workspace_id
        self.circuit_enabled = circuit_enabled

    async def scalar(self, statement):
        statement_text = str(statement)
        if "withdrawal_circuit_breakers" in statement_text and self.circuit_enabled:
            return type("Circuit", (), {"state": "enabled"})()
        if "workspaces" in statement_text:
            return type("Workspace", (), {"id": self.workspace_id})()
        return None

    async def get(self, model, identifier):
        del model
        return self.actor if self.actor is not None and identifier == self.actor.id else None

    def add(self, item):
        if getattr(item, "id", None) is None:
            item.id = uuid4()
        self.items.append(item)

    async def flush(self):
        return None

    async def commit(self):
        return None


@pytest.mark.asyncio
async def test_qtrade_is_auditable_and_disabled_after_risk_passes() -> None:
    item = signal()
    actor = User(id=item.actor_id, email="actor@example.com", hashed_password="x", first_name="Actor", last_name="User", is_verified=True)
    session = FakeSession(actor=actor, workspace_id=item.workspace_id)
    result = await QTradeExecutionService(
        session, risk_provider=FakeRisk(), limits=RiskLimits(maximum_risk=Decimal("50")), context_authorizer=AllowContext(), execution_enabled=False,
    ).evaluate(tenant_id=item.tenant_id, actor=actor, signal=item, idempotency_key="trade-1")
    assert result.state == ExecutionState.BLOCKED.value
    assert result.execution_permission is False
    assert result.rejection_reason == "QTRADE execution is disabled"
    assert result.dry_run_result["status"] == "DRY_RUN"
    assert result.dry_run_result["not_executed"] is True
    audit = next(row for row in session.items if getattr(row, "event_type", None) == "QTRADE_EXECUTION_DECISION")
    assert audit.event_metadata["risk_inputs"]["daily_loss"] == "0"


@pytest.mark.asyncio
async def test_qtrade_missing_circuit_state_fails_closed_and_is_audited():
    item = signal()
    actor = User(id=item.actor_id, email="actor-missing-circuit@example.com", hashed_password="x", first_name="Actor", last_name="User", is_verified=True)
    session = FakeSession(actor=actor, workspace_id=item.workspace_id, circuit_enabled=False)
    result = await QTradeExecutionService(
        session, risk_provider=FakeRisk(), limits=RiskLimits(maximum_risk=Decimal("50")), context_authorizer=AllowContext(), execution_enabled=False,
    ).evaluate(tenant_id=item.tenant_id, actor=actor, signal=item, idempotency_key="trade-missing-circuit")
    assert result.state == ExecutionState.BLOCKED.value
    assert result.rejection_reason == "Circuit breaker state is unavailable"
    assert result.dry_run_result["status"] == "NOT_EXECUTED"


@pytest.mark.asyncio
async def test_qtrade_rejects_unavailable_execution_account():
    item = signal(account_id=uuid4())
    actor = User(id=item.actor_id, email="actor-account@example.com", hashed_password="x", first_name="Actor", last_name="User", is_verified=True)
    session = FakeSession(actor=actor, workspace_id=item.workspace_id)
    with pytest.raises(RiskValidationError, match="account is unavailable"):
        await QTradeExecutionService(
            session, risk_provider=FakeRisk(), limits=RiskLimits(maximum_risk=Decimal("50")), context_authorizer=AllowContext(), execution_enabled=False,
        ).evaluate(tenant_id=item.tenant_id, actor=actor, signal=item, idempotency_key="trade-account-mismatch")


@pytest.mark.asyncio
async def test_qtrade_without_authoritative_tenant_context_fails_closed():
    with pytest.raises(TypeError):
        QTradeExecutionService(FakeSession(), risk_provider=FakeRisk(), limits=RiskLimits(maximum_risk=Decimal("50")), execution_enabled=False)


def test_qtrade_cannot_be_enabled_by_constructor_flag() -> None:
    with pytest.raises(RuntimeError, match="not enabled"):
        QTradeExecutionService(FakeSession(), risk_provider=FakeRisk(), limits=RiskLimits(Decimal("1")), context_authorizer=AllowContext(), execution_enabled=True)
