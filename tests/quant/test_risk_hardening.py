from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.quant.contracts import MarketRegime, ResearchProvenance, ResearchSignal
from app.services.risk_contract import (
    AuthoritativeRiskInputs,
    PortfolioRiskProvider,
    RiskEngine,
    RiskExecutionContext,
    RiskInputUnavailableError,
    RiskLimits,
    RiskProvenance,
    RiskSnapshot,
    TrustedRiskSnapshotBuilder,
    _issue_risk_authority,
    RiskValidationError,
    ValuationUnavailableError,
)


def _signal(*, tenant_id=None, workspace_id=None, actor_id=None, account_id=None):
    timestamp = datetime.now(UTC)
    return ResearchSignal(
        signal_id=uuid4(), tenant_id=tenant_id or uuid4(), workspace_id=workspace_id or uuid4(),
        actor_id=actor_id or uuid4(), account_id=account_id or uuid4(), asset="BTCUSD",
        strategy="trend", timeframe="1h", regime=MarketRegime.TRENDING, direction="long",
        confidence=Decimal("0.8"), entry=Decimal("100"), stop=Decimal("90"),
        targets=(Decimal("120"),), expected_risk_reward=Decimal("2"),
        rationale="verified", timestamp=timestamp,
        provenance=ResearchProvenance("provider", "source", "BTCUSD", timestamp, timestamp, "run"),
    )


def _snapshot(signal, **overrides):
    values = dict(
        tenant_id=signal.tenant_id, workspace_id=signal.workspace_id,
        account_id=signal.account_id, actor_id=signal.actor_id,
        total_equity=Decimal("1000"), available_capital=Decimal("500"),
        reserved_capital=Decimal("0"), current_exposure=Decimal("0"),
        asset_exposure={"BTCUSD": Decimal("0")}, concentration={"BTCUSD": Decimal("0")}, realized_pnl=Decimal("0"),
        unrealized_pnl=Decimal("0"), daily_loss=Decimal("0"), strategy_exposure={"trend": Decimal("0")},
    )
    values.update(overrides)
    values.setdefault("provenance", RiskProvenance("test", datetime.now(UTC), RiskEngine.REQUIRED_INPUTS, valuation_source="ledger-fixture"))
    return TrustedRiskSnapshotBuilder(_issue_risk_authority()).build(AuthoritativeRiskInputs(**values))


def _validate(signal, snapshot):
    return RiskEngine().validate(
        signal=signal, snapshot=snapshot,
        limits=RiskLimits(maximum_risk=Decimal("50")),
        context=RiskExecutionContext(signal.tenant_id, signal.workspace_id, signal.actor_id, signal.account_id),
    )


@pytest.mark.parametrize(
    "field",
    ("current_exposure", "concentration", "realized_pnl", "unrealized_pnl", "daily_loss", "strategy_exposure"),
)
def test_unknown_authoritative_risk_input_fails_closed(field):
    signal = _signal()
    with pytest.raises(RiskInputUnavailableError, match=field):
        _validate(signal, _snapshot(signal, **{field: None}))


def test_authoritative_zero_risk_inputs_are_distinct_from_unknown():
    signal = _signal()
    decision = _validate(signal, _snapshot(signal))
    assert decision.allowed is True


def test_untrusted_snapshot_with_zero_defaults_fails_closed():
    signal = _signal()
    snapshot = RiskSnapshot(
        tenant_id=signal.tenant_id, workspace_id=signal.workspace_id, account_id=signal.account_id,
        actor_id=signal.actor_id, total_equity=Decimal("1000"), available_capital=Decimal("500"),
        reserved_capital=Decimal("0"), current_exposure=Decimal("0"), asset_exposure={},
        concentration={}, realized_pnl=Decimal("0"), unrealized_pnl=Decimal("0"),
        daily_loss=Decimal("0"), strategy_exposure={},
    )
    with pytest.raises(RiskInputUnavailableError, match="risk_provenance"):
        _validate(signal, snapshot)


def test_missing_risk_provenance_input_fails_closed():
    signal = _signal()
    snapshot = _snapshot(signal, provenance=RiskProvenance("test", datetime.now(UTC), frozenset({"daily_loss"}), valuation_source="ledger-fixture"))
    with pytest.raises(RiskInputUnavailableError, match="risk_provenance:concentration"):
        _validate(signal, snapshot)


@pytest.mark.parametrize("mismatch", ("tenant_id", "workspace_id", "account_id", "actor_id"))
def test_risk_rejects_identity_mismatch(mismatch):
    signal = _signal()
    snapshot = _snapshot(signal, **{mismatch: uuid4()})
    with pytest.raises(RiskValidationError, match="identity"):
        _validate(signal, snapshot)


@pytest.mark.asyncio
async def test_portfolio_risk_provider_fails_closed_without_daily_loss_source():
    signal = _signal()

    class Portfolio:
        async def summary(self, **kwargs):
            assert kwargs == {"user_id": signal.account_id, "workspace_id": signal.workspace_id, "actor_id": signal.actor_id, "require_authoritative_context": True}
            return SimpleNamespace(
                workspace_id=signal.workspace_id, user_id=signal.account_id,
                balances=(("USD", SimpleNamespace(
                    authoritative_balance=Decimal("1000"), available_balance=Decimal("1000"),
                    reserved_balance=Decimal("0"),
                )),), positions=(),
            )

    with pytest.raises(RiskInputUnavailableError, match="daily_loss"):
        await PortfolioRiskProvider(Portfolio()).snapshot(
            tenant_id=signal.tenant_id, workspace_id=signal.workspace_id,
            actor_id=signal.actor_id, account_id=signal.account_id,
        )


@pytest.mark.asyncio
async def test_multi_asset_valuation_without_provider_fails_closed():
    signal = _signal()

    class Portfolio:
        async def summary(self, **kwargs):
            del kwargs
            balance = SimpleNamespace(authoritative_balance=Decimal("1"), available_balance=Decimal("1"), reserved_balance=Decimal("0"))
            return SimpleNamespace(workspace_id=signal.workspace_id, user_id=signal.account_id, balances=(("BTC", balance), ("USD", balance)), positions=())

    with pytest.raises(ValuationUnavailableError, match="Multi-asset"):
        await PortfolioRiskProvider(Portfolio()).snapshot(
            tenant_id=signal.tenant_id, workspace_id=signal.workspace_id,
            actor_id=signal.actor_id, account_id=signal.account_id,
        )
