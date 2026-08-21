from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from app.quant.contracts import MarketRegime, QLabUnavailableError


class EvidenceState(StrEnum):
    NOT_CONFIGURED = "not_configured"
    DATA_UNAVAILABLE = "data_unavailable"
    EVIDENCE_INSUFFICIENT = "evidence_insufficient"
    SIGNAL_REJECTED = "signal_rejected"
    SIGNAL_READY = "signal_ready"


@dataclass(frozen=True, slots=True)
class OHLCVBar:
    symbol: str
    timeframe: str
    observed_at: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal | None = None

    def __post_init__(self) -> None:
        if not self.symbol or not self.timeframe:
            raise ValueError("OHLC evidence requires symbol and timeframe")
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("OHLC timestamp must be timezone-aware")
        if min(self.open, self.high, self.low, self.close) <= 0:
            raise ValueError("OHLC values must be positive")
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close) or self.high < self.low:
            raise ValueError("OHLC bar is malformed")
        if self.volume is not None and self.volume < 0:
            raise ValueError("Volume cannot be negative")


@dataclass(frozen=True, slots=True)
class StructuralEvidence:
    symbol: str
    timeframe: str
    provider: str
    source: str
    observed_at: datetime
    generated_at: datetime
    provenance: str
    freshness_status: str
    regime: MarketRegime
    liquidity_sweep: bool
    market_structure_shift: bool
    pullback_retest: bool
    direction: str | None
    entry: Decimal
    stop: Decimal
    range_high: Decimal
    range_low: Decimal


def build_structural_evidence(*, bars: tuple[OHLCVBar, ...], provider: str, source: str, provenance: str, generated_at: datetime, max_age_seconds: int) -> StructuralEvidence:
    if not bars:
        raise QLabUnavailableError("DATA_UNAVAILABLE: no candles supplied")
    if len(bars) < 20:
        raise QLabUnavailableError("EVIDENCE_INSUFFICIENT: at least 20 candles are required")
    if not provider or not source or not provenance:
        raise QLabUnavailableError("NOT_CONFIGURED: market provenance is incomplete")
    if generated_at.tzinfo is None or generated_at.utcoffset() is None:
        raise ValueError("Generated timestamp must be timezone-aware")
    ordered = tuple(sorted(bars, key=lambda bar: bar.observed_at))
    if ordered != bars or any(bar.symbol != bars[0].symbol or bar.timeframe != bars[0].timeframe for bar in bars):
        raise QLabUnavailableError("EVIDENCE_INSUFFICIENT: candle identity or ordering is invalid")
    age = (generated_at - bars[-1].observed_at).total_seconds()
    if age < 0 or age > max_age_seconds:
        raise QLabUnavailableError("DATA_UNAVAILABLE: candle data is stale")
    prior = bars[-6:-1]
    latest = bars[-1]
    prior_low = min(bar.low for bar in prior)
    prior_high = max(bar.high for bar in prior)
    liquidity_sweep = latest.low < prior_low and latest.close > prior_low or latest.high > prior_high and latest.close < prior_high
    mss = latest.close > prior_high or latest.close < prior_low
    pullback = min(latest.open, latest.close) <= prior_high and max(latest.open, latest.close) >= prior_low
    direction = "long" if mss and latest.close > prior_high else "short" if mss else None
    stop = latest.low if direction == "long" else latest.high if direction == "short" else latest.close
    return StructuralEvidence(
        symbol=latest.symbol, timeframe=latest.timeframe, provider=provider, source=source,
        observed_at=latest.observed_at, generated_at=generated_at, provenance=provenance,
        freshness_status="FRESH", regime=MarketRegime.VOLATILE if latest.high - latest.low > (prior_high - prior_low) * Decimal("1.5") else MarketRegime.RANGE_BOUND,
        liquidity_sweep=liquidity_sweep, market_structure_shift=mss, pullback_retest=pullback,
        direction=direction, entry=latest.close, stop=stop, range_high=prior_high, range_low=prior_low,
    )
