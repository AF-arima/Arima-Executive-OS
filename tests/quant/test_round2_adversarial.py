from datetime import UTC, datetime, timedelta
from dataclasses import replace
from decimal import Decimal
from uuid import uuid4

import pytest

from app.quant.contracts import MarketRegime, ResearchProvenance, ResearchSignal
from app.services.risk_contract import (
    AuthoritativeRiskInputs,
    RiskEngine,
    RiskExecutionContext,
    RiskInputUnavailableError,
    RiskLimits,
    RiskProvenance,
    RiskSnapshot,
    RiskSnapshotAuthorityError,
    RiskValidationError,
    TrustedRiskSnapshotBuilder,
    _issue_risk_authority,
)
from app.services.operations import withdrawal_request_fingerprint
from app.services.trading_contracts import execution_request_fingerprint


def signal() -> ResearchSignal:
    now = datetime.now(UTC)
    tenant_id, workspace_id, actor_id, account_id = uuid4(), uuid4(), uuid4(), uuid4()
    return ResearchSignal(
        signal_id=uuid4(), tenant_id=tenant_id, workspace_id=workspace_id,
        actor_id=actor_id, account_id=account_id, asset="BTCUSD", strategy="test",
        timeframe="1h", regime=MarketRegime.TRENDING, direction="long",
        confidence=Decimal("0.8"), entry=Decimal("100"), stop=Decimal("90"),
        targets=(Decimal("120"),), expected_risk_reward=Decimal("2"), rationale="test",
        timestamp=now, provenance=ResearchProvenance("provider", "source", "BTCUSD", now, now, "run"),
    )


def inputs(item: ResearchSignal, *, calculated_at: datetime | None = None) -> AuthoritativeRiskInputs:
    return AuthoritativeRiskInputs(
        tenant_id=item.tenant_id, workspace_id=item.workspace_id, account_id=item.account_id,
        actor_id=item.actor_id, total_equity=Decimal("1000"), available_capital=Decimal("500"),
        reserved_capital=Decimal("0"), current_exposure=Decimal("0"), asset_exposure={"BTCUSD": Decimal("0")},
        concentration={"BTCUSD": Decimal("0")}, realized_pnl=Decimal("0"), unrealized_pnl=Decimal("0"),
        daily_loss=Decimal("0"), strategy_exposure={"test": Decimal("0")},
        provenance=RiskProvenance(
            "authoritative-ledger", calculated_at or datetime.now(UTC),
            RiskEngine.REQUIRED_INPUTS, valuation_source="approved-valuation", freshness_seconds=300,
        ),
    )


def test_public_factory_fabrication_is_removed() -> None:
    assert not hasattr(RiskSnapshot, "from_authoritative")


def test_trusted_builder_accepts_legitimate_zero_values() -> None:
    item = signal()
    snapshot = TrustedRiskSnapshotBuilder(_issue_risk_authority()).build(inputs(item))
    decision = RiskEngine().validate(
        signal=item, snapshot=snapshot, limits=RiskLimits(maximum_risk=Decimal("50")),
        context=RiskExecutionContext(item.tenant_id, item.workspace_id, item.actor_id, item.account_id),
    )
    assert decision.allowed
    assert snapshot.current_exposure == Decimal("0")
    assert snapshot.daily_loss == Decimal("0")


def test_invalid_authority_capability_is_rejected() -> None:
    with pytest.raises(RiskSnapshotAuthorityError):
        TrustedRiskSnapshotBuilder(type("Capability", (), {"_marker": object()})())


def test_stale_authoritative_input_fails_closed() -> None:
    item = signal()
    stale = inputs(item, calculated_at=datetime.now(UTC) - timedelta(seconds=301))
    with pytest.raises(RiskInputUnavailableError, match="stale"):
        TrustedRiskSnapshotBuilder(_issue_risk_authority()).build(stale)


def test_provenance_identity_mismatch_fails_closed() -> None:
    item = signal()
    base = inputs(item)
    mismatched = replace(
        base.provenance,
        tenant_id=uuid4(),
    )
    with pytest.raises(RiskValidationError, match="provenance identity"):
        TrustedRiskSnapshotBuilder(_issue_risk_authority()).build(replace(base, provenance=mismatched))


def test_identity_mismatch_fails_at_risk_engine() -> None:
    item = signal()
    snapshot = TrustedRiskSnapshotBuilder(_issue_risk_authority()).build(
        replace(inputs(item), account_id=uuid4())
    )
    with pytest.raises(RiskValidationError, match="identity"):
        RiskEngine().validate(
            signal=item, snapshot=snapshot, limits=RiskLimits(maximum_risk=Decimal("50")),
            context=RiskExecutionContext(item.tenant_id, item.workspace_id, item.actor_id, item.account_id),
        )


def test_idempotency_fingerprint_changes_for_material_payload() -> None:
    class Request:
        amount = Decimal("1")
        currency = "ETH"
        destination_wallet_address = "0xabc"
        network = "Ethereum Mainnet"
        risk_acknowledgement = True
        first_name = "A"
        last_name = "B"
        idempotency_key = "same-key"

    user_id, workspace_id = uuid4(), uuid4()
    first = withdrawal_request_fingerprint(Request(), user_id=user_id, workspace_id=workspace_id)
    Request.amount = Decimal("2")
    second = withdrawal_request_fingerprint(Request(), user_id=user_id, workspace_id=workspace_id)
    assert first != second


def test_execution_fingerprint_changes_for_material_signal() -> None:
    item = signal()
    changed = ResearchSignal(
        signal_id=item.signal_id, tenant_id=item.tenant_id, workspace_id=item.workspace_id,
        actor_id=item.actor_id, account_id=item.account_id, asset=item.asset, strategy=item.strategy,
        timeframe=item.timeframe, regime=item.regime, direction=item.direction,
        confidence=item.confidence, entry=item.entry, stop=Decimal("80"), targets=item.targets,
        expected_risk_reward=item.expected_risk_reward, rationale=item.rationale, timestamp=item.timestamp,
        provenance=item.provenance,
    )
    assert execution_request_fingerprint(item, tenant_id=item.tenant_id, actor_id=item.actor_id) != execution_request_fingerprint(changed, tenant_id=item.tenant_id, actor_id=item.actor_id)
