from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from app.quant.contracts import MarketRegime, QLabUnavailableError
from app.quant.evidence import StructuralEvidence
from app.quant.market_adapter import ARIMAStrategyEvidenceProvider, MarketObservation, StrategyEvidence, normalize_strategy_evidence
from app.quant.strategy_evidence import (
    ConfiguredSessionProvider,
    NewsEvaluation,
    NewsEvent,
    NotConfiguredNewsProvider,
    SessionEvaluation,
    StrategyEvidenceProvenance,
    StrategyEvidenceState,
    TradingWindow,
)


def _evidence(**overrides: object) -> StrategyEvidence:
    values: dict[str, object] = {
        "direction": "long", "entry": Decimal("100"), "stop": Decimal("90"),
        "targets": (Decimal("120"),), "confidence": Decimal("0.8"),
        "expected_risk_reward": Decimal("2"), "strategy": "arima",
        "timeframe": "1h", "regime": MarketRegime.TRENDING, "rationale": "verified",
        "liquidity_sweep_confirmed": True, "mss_confirmed": True,
        "pullback_confirmed": True, "news_clear": True, "session_allowed": True,
        "provenance": StrategyEvidenceProvenance("market", "structure", "session", "news", datetime.now(UTC)),
    }
    values.update(overrides)
    return StrategyEvidence(**values)


def test_session_provider_evaluates_london_new_york_weekend_and_outside_window() -> None:
    provider = ConfiguredSessionProvider()
    assert provider.evaluate(timestamp=datetime(2026, 8, 17, 9, tzinfo=UTC)).session == "LONDON"
    assert provider.evaluate(timestamp=datetime(2026, 8, 17, 18, tzinfo=UTC)).session == "NEW_YORK"
    weekend = provider.evaluate(timestamp=datetime(2026, 8, 15, 12, tzinfo=UTC))
    assert weekend.state is StrategyEvidenceState.SESSION_BLOCKED
    assert weekend.allowed is False
    outside = provider.evaluate(timestamp=datetime(2026, 8, 17, 23, tzinfo=UTC))
    assert outside.state is StrategyEvidenceState.SESSION_BLOCKED
    assert outside.provenance.startswith("configured_session_calendar")


def test_session_provider_uses_configured_timezone_and_rejects_naive_clock() -> None:
    provider = ConfiguredSessionProvider((TradingWindow("LONDON", "Europe/London", start=datetime.min.time().replace(hour=8), end=datetime.min.time().replace(hour=17)),))
    result = provider.evaluate(timestamp=datetime(2026, 1, 5, 9, tzinfo=UTC))
    assert result.timezone == "Europe/London"
    with pytest.raises(ValueError, match="timezone-aware"):
        provider.evaluate(timestamp=datetime(2026, 1, 5, 9))


@pytest.mark.asyncio
async def test_missing_news_provider_is_explicitly_not_configured() -> None:
    now = datetime(2026, 8, 17, 12, tzinfo=UTC)
    result = await NotConfiguredNewsProvider().evaluate(asset="BTCUSD", timestamp=now)
    assert result.state is StrategyEvidenceState.NOT_CONFIGURED
    assert result.clear is False
    assert result.events == ()
    assert result.provenance == "news_provider:not_configured"


@pytest.mark.asyncio
async def test_news_event_contract_preserves_source_and_stale_event_can_block() -> None:
    event = NewsEvent("US CPI", datetime(2026, 8, 17, 12, tzinfo=UTC), "USD", "HIGH", "approved", "news-1")
    evaluation = NewsEvaluation(False, StrategyEvidenceState.NEWS_BLOCKED, "High-impact event window", datetime(2026, 8, 17, 12, tzinfo=UTC), (event,), "approved:news-1")
    assert evaluation.events[0].affected_market == "USD"
    assert evaluation.state is StrategyEvidenceState.NEWS_BLOCKED


def test_signal_rejects_missing_and_blocked_strategy_evidence() -> None:
    observation = MarketObservation("provider", "BTC/USD", Decimal("100"), datetime.now(UTC), "market-1")
    with pytest.raises(QLabUnavailableError) as missing:
        normalize_strategy_evidence(observation=observation, tenant_id=uuid4(), workspace_id=uuid4(), actor_id=uuid4(), account_id=uuid4(), asset="BTCUSD", evidence=_evidence(provenance=None))
    assert missing.value.state == StrategyEvidenceState.EVIDENCE_INSUFFICIENT.value
    blocked_session = _evidence(session=type("Session", (), {"state": StrategyEvidenceState.SESSION_BLOCKED, "reason": "Weekend"})())
    with pytest.raises(QLabUnavailableError) as session_error:
        normalize_strategy_evidence(observation=observation, tenant_id=uuid4(), workspace_id=uuid4(), actor_id=uuid4(), account_id=uuid4(), asset="BTCUSD", evidence=blocked_session)
    assert session_error.value.state == StrategyEvidenceState.SESSION_BLOCKED.value
    blocked_news = _evidence(news=type("News", (), {"state": StrategyEvidenceState.NOT_CONFIGURED, "reason": "No provider"})())
    with pytest.raises(QLabUnavailableError) as news_error:
        normalize_strategy_evidence(observation=observation, tenant_id=uuid4(), workspace_id=uuid4(), actor_id=uuid4(), account_id=uuid4(), asset="BTCUSD", evidence=blocked_news)
    assert news_error.value.state == StrategyEvidenceState.NEWS_BLOCKED.value


@pytest.mark.parametrize("field", ["liquidity_sweep_confirmed", "mss_confirmed", "pullback_confirmed"])
def test_signal_rejects_invalid_structural_confirmation(field: str) -> None:
    observation = MarketObservation("provider", "BTC/USD", Decimal("100"), datetime.now(UTC), "market-1")
    with pytest.raises(QLabUnavailableError) as error:
        normalize_strategy_evidence(observation=observation, tenant_id=uuid4(), workspace_id=uuid4(), actor_id=uuid4(), account_id=uuid4(), asset="BTCUSD", evidence=_evidence(**{field: False}))
    assert error.value.state == StrategyEvidenceState.EVIDENCE_INSUFFICIENT.value


def test_signal_rejects_rr_below_two() -> None:
    observation = MarketObservation("provider", "BTC/USD", Decimal("100"), datetime.now(UTC), "market-1")
    with pytest.raises(QLabUnavailableError) as error:
        normalize_strategy_evidence(observation=observation, tenant_id=uuid4(), workspace_id=uuid4(), actor_id=uuid4(), account_id=uuid4(), asset="BTCUSD", evidence=_evidence(expected_risk_reward=Decimal("1.99")))
    assert error.value.state == StrategyEvidenceState.SIGNAL_REJECTED.value


@pytest.mark.asyncio
async def test_arima_provider_derives_levels_only_from_verified_structure() -> None:
    now = datetime(2026, 8, 17, 12, tzinfo=UTC)
    session = SessionEvaluation("LONDON", True, "Within window", now, "Europe/London", "session-1", StrategyEvidenceState.SIGNAL_READY)
    news = NewsEvaluation(True, StrategyEvidenceState.SIGNAL_READY, "No blocking event", now, (), "news-1")
    structural = StructuralEvidence(
        symbol="BTC/USD", timeframe="1h", provider="twelve_data", source="twelve_data",
        observed_at=now, generated_at=now, provenance="ohlc-1", freshness_status="FRESH",
        regime=MarketRegime.TRENDING, liquidity_sweep=True, market_structure_shift=True,
        pullback_retest=True, direction="long", entry=Decimal("100"), stop=Decimal("98"),
        range_high=Decimal("99"), range_low=Decimal("97"),
    )
    result = await ARIMAStrategyEvidenceProvider().analyze(
        observation=MarketObservation("twelve_data", "BTC/USD", Decimal("100"), now, "ohlc-1"),
        structural=structural, session=session, news=news, asset="BTCUSD", strategy="arima",
    )
    assert result is not None
    assert result.targets == (Decimal("104"),)
    assert result.provenance is not None


@pytest.mark.asyncio
async def test_arima_provider_returns_no_signal_without_structural_confirmation() -> None:
    now = datetime(2026, 8, 17, 12, tzinfo=UTC)
    session = SessionEvaluation("LONDON", True, "Within window", now, "Europe/London", "session-1", StrategyEvidenceState.SIGNAL_READY)
    news = NewsEvaluation(True, StrategyEvidenceState.SIGNAL_READY, "No blocking event", now, (), "news-1")
    structural = StructuralEvidence(
        symbol="BTC/USD", timeframe="1h", provider="twelve_data", source="twelve_data",
        observed_at=now, generated_at=now, provenance="ohlc-1", freshness_status="FRESH",
        regime=MarketRegime.RANGE_BOUND, liquidity_sweep=False, market_structure_shift=True,
        pullback_retest=True, direction="long", entry=Decimal("100"), stop=Decimal("98"),
        range_high=Decimal("99"), range_low=Decimal("97"),
    )
    result = await ARIMAStrategyEvidenceProvider().analyze(
        observation=MarketObservation("twelve_data", "BTC/USD", Decimal("100"), now, "ohlc-1"),
        structural=structural, session=session, news=news, asset="BTCUSD", strategy="arima",
    )
    assert result is None
