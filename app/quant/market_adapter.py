from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Protocol
from uuid import UUID, uuid4

from app.database.models import User
from app.market.config import CanonicalInstrument
from app.market.gateway import MarketDataGateway
from app.quant.contracts import MarketRegime, QLabResearchProvider, QLabUnavailableError, ResearchProvenance, ResearchSignal
from app.quant.evidence import OHLCVBar, StructuralEvidence, build_structural_evidence
from app.quant.strategy_evidence import (
    ConfiguredSessionProvider,
    NewsEvaluation,
    NewsProvider,
    NotConfiguredNewsProvider,
    SessionEvaluation,
    SessionProvider,
    StrategyEvidenceProvenance,
    StrategyEvidenceState,
)


@dataclass(frozen=True, slots=True)
class MarketObservation:
    provider: str
    symbol: str
    price: Decimal
    timestamp: datetime
    provenance_reference: str
    status: str = "VERIFIED_FRESH"

    def __post_init__(self) -> None:
        if not self.provider or not self.symbol or not self.provenance_reference:
            raise ValueError("Market observation provenance is incomplete")
        if self.price <= 0:
            raise ValueError("Market observation price must be positive")
        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() is None:
            raise ValueError("Market observation timestamp must be timezone-aware")
        if self.status != "VERIFIED_FRESH":
            raise ValueError("Only verified fresh observations can reach QLab")


@dataclass(frozen=True, slots=True)
class StrategyEvidence:
    direction: str
    entry: Decimal
    stop: Decimal
    targets: tuple[Decimal, ...]
    confidence: Decimal
    expected_risk_reward: Decimal
    strategy: str
    timeframe: str
    regime: MarketRegime
    rationale: str
    liquidity_sweep_confirmed: bool
    mss_confirmed: bool
    pullback_confirmed: bool
    news_clear: bool
    session_allowed: bool
    session: SessionEvaluation | None = None
    news: NewsEvaluation | None = None
    provenance: StrategyEvidenceProvenance | None = None


class StrategyEvidenceProvider(Protocol):
    async def analyze(
        self, *, observation: MarketObservation, structural: StructuralEvidence,
        session: SessionEvaluation, news: NewsEvaluation, asset: str, strategy: str,
    ) -> StrategyEvidence | None: ...


class ARIMAStrategyEvidenceProvider:
    """Deterministic ARIMA rule adapter over verified structural evidence.

    It derives levels only from the verified candle set. It never supplies a
    fallback price or signal when the required structural confirmations are
    absent.
    """

    async def analyze(
        self, *, observation: MarketObservation, structural: StructuralEvidence,
        session: SessionEvaluation, news: NewsEvaluation, asset: str, strategy: str,
    ) -> StrategyEvidence | None:
        del observation, asset
        if not all((structural.liquidity_sweep, structural.market_structure_shift, structural.pullback_retest)):
            return None
        if structural.direction is None or (
            structural.direction == "long" and structural.entry <= structural.stop
        ) or (
            structural.direction == "short" and structural.entry >= structural.stop
        ):
            return None
        risk = abs(structural.entry - structural.stop)
        target = structural.entry + (risk * Decimal("2")) if structural.direction == "long" else structural.entry - (risk * Decimal("2"))
        return StrategyEvidence(
            direction=structural.direction, entry=structural.entry, stop=structural.stop,
            targets=(target,), confidence=Decimal("0.75"), expected_risk_reward=Decimal("2"),
            strategy=strategy, timeframe=structural.timeframe, regime=structural.regime,
            rationale="Verified liquidity sweep, MSS close, and pullback/retest",
            liquidity_sweep_confirmed=structural.liquidity_sweep,
            mss_confirmed=structural.market_structure_shift,
            pullback_confirmed=structural.pullback_retest,
            news_clear=news.clear, session_allowed=session.allowed,
            session=session, news=news,
            provenance=StrategyEvidenceProvenance(
                market_reference=structural.provenance,
                structural_reference=structural.provenance,
                session_reference=session.provenance,
                news_reference=news.provenance,
                generated_at=structural.generated_at,
            ),
        )


def normalize_strategy_evidence(*, observation: MarketObservation, tenant_id: UUID, workspace_id: UUID, actor_id: UUID, account_id: UUID, asset: str, evidence: StrategyEvidence) -> ResearchSignal:
    if evidence.provenance is None:
        raise QLabUnavailableError("Strategy evidence provenance is incomplete", state=StrategyEvidenceState.EVIDENCE_INSUFFICIENT.value)
    if evidence.session is not None and evidence.session.state is StrategyEvidenceState.SESSION_BLOCKED:
        raise QLabUnavailableError(evidence.session.reason, state=StrategyEvidenceState.SESSION_BLOCKED.value)
    if evidence.news is not None and evidence.news.state is not StrategyEvidenceState.SIGNAL_READY:
        raise QLabUnavailableError(evidence.news.reason, state=StrategyEvidenceState.NEWS_BLOCKED.value)
    if not all((evidence.liquidity_sweep_confirmed, evidence.mss_confirmed, evidence.pullback_confirmed, evidence.news_clear, evidence.session_allowed)):
        raise QLabUnavailableError("ARIMA strategy confirmations are incomplete", state=StrategyEvidenceState.EVIDENCE_INSUFFICIENT.value)
    if evidence.expected_risk_reward < Decimal("2"):
        raise QLabUnavailableError("Signal risk/reward is below the minimum 1:2 rule", state=StrategyEvidenceState.SIGNAL_REJECTED.value)
    now = observation.timestamp
    return ResearchSignal(
        signal_id=uuid4(), tenant_id=tenant_id, workspace_id=workspace_id,
        actor_id=actor_id, account_id=account_id,
        asset=asset, strategy=evidence.strategy, regime=evidence.regime,
        timeframe=evidence.timeframe,
        direction=evidence.direction, confidence=evidence.confidence,
        entry=evidence.entry, stop=evidence.stop, targets=evidence.targets,
        expected_risk_reward=evidence.expected_risk_reward, rationale=evidence.rationale,
        timestamp=now,
        provenance=ResearchProvenance(observation.provider, observation.provider, observation.symbol, now, now, observation.provenance_reference),
    )


class GatewayQLabResearchProvider(QLabResearchProvider):
    """QLab adapter using the approved market gateway and verified strategy evidence."""

    def __init__(
        self, gateway: MarketDataGateway, evidence_provider: StrategyEvidenceProvider | None = None,
        session_provider: SessionProvider | None = None, news_provider: NewsProvider | None = None,
    ) -> None:
        self.gateway = gateway
        self.evidence_provider = evidence_provider or ARIMAStrategyEvidenceProvider()
        self.session_provider = session_provider or ConfiguredSessionProvider()
        self.news_provider = news_provider or NotConfiguredNewsProvider()

    async def research(self, *, tenant_id: UUID, workspace_id: UUID, actor_id: UUID, account_id: UUID, user: object, run_id: UUID, asset: str, strategy: str) -> ResearchSignal:
        try:
            canonical = CanonicalInstrument(asset.upper())
        except ValueError as error:
            raise QLabUnavailableError("Market symbol mapping is not verified") from error
        if not isinstance(user, User):
            raise QLabUnavailableError("QLab requires an authenticated user context")
        result = await self.gateway.historical_candles(
            canonical=canonical, user=user, workspace_id=workspace_id, run_id=run_id,
            timeframe="1h", limit=120,
        )
        latest = result.candles[-1]
        observation = MarketObservation(
            provider=result.provider, symbol=latest.symbol, price=latest.close,
            timestamp=latest.observed_at, provenance_reference=latest.provenance,
        )
        bars = tuple(OHLCVBar(
            symbol=candle.symbol, timeframe=candle.timeframe, observed_at=candle.observed_at,
            open=candle.open, high=candle.high, low=candle.low, close=candle.close, volume=candle.volume,
        ) for candle in result.candles)
        structural = build_structural_evidence(
            bars=bars, provider=result.provider, source=result.source,
            provenance=latest.provenance, generated_at=datetime.now(UTC), max_age_seconds=86_400,
        )
        session = self.session_provider.evaluate(timestamp=datetime.now(UTC))
        if not session.allowed:
            raise QLabUnavailableError(session.reason, state=StrategyEvidenceState.SESSION_BLOCKED.value)
        news = await self.news_provider.evaluate(asset=canonical.value, timestamp=datetime.now(UTC))
        if news.state is not StrategyEvidenceState.SIGNAL_READY or not news.clear:
            raise QLabUnavailableError(news.reason, state=StrategyEvidenceState.NEWS_BLOCKED.value)
        evidence = await self.evidence_provider.analyze(
            observation=observation, structural=structural, session=session, news=news,
            asset=canonical.value, strategy=strategy,
        )
        if evidence is None:
            raise QLabUnavailableError("Strategy evidence is unavailable", state=StrategyEvidenceState.EVIDENCE_INSUFFICIENT.value)
        signal = normalize_strategy_evidence(
            observation=observation, tenant_id=tenant_id, workspace_id=workspace_id,
            actor_id=actor_id, account_id=account_id,
            asset=canonical.value, evidence=evidence,
        )
        provider_provenance = signal.provenance.__class__(
            result.provider, result.source, latest.symbol, latest.observed_at,
            observation.timestamp, observation.provenance_reference,
        )
        return ResearchSignal(
            signal_id=signal.signal_id, tenant_id=signal.tenant_id,
            workspace_id=signal.workspace_id, actor_id=signal.actor_id,
            account_id=signal.account_id,
            asset=signal.asset, strategy=signal.strategy, regime=signal.regime,
            timeframe=signal.timeframe,
            direction=signal.direction, confidence=signal.confidence, entry=signal.entry,
            stop=signal.stop, targets=signal.targets, expected_risk_reward=signal.expected_risk_reward,
            rationale=signal.rationale, timestamp=signal.timestamp, provenance=provider_provenance,
        )
