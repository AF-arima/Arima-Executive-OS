from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time
from enum import StrEnum
from typing import Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class StrategyEvidenceState(StrEnum):
    NOT_CONFIGURED = "NOT_CONFIGURED"
    DATA_UNAVAILABLE = "DATA_UNAVAILABLE"
    SESSION_BLOCKED = "SESSION_BLOCKED"
    NEWS_BLOCKED = "NEWS_BLOCKED"
    EVIDENCE_INSUFFICIENT = "EVIDENCE_INSUFFICIENT"
    SIGNAL_REJECTED = "SIGNAL_REJECTED"
    SIGNAL_READY = "SIGNAL_READY"


@dataclass(frozen=True, slots=True)
class StrategyEvidenceProvenance:
    market_reference: str
    structural_reference: str
    session_reference: str
    news_reference: str
    generated_at: datetime

    def __post_init__(self) -> None:
        if not all((self.market_reference, self.structural_reference, self.session_reference, self.news_reference)):
            raise ValueError("Strategy evidence provenance is incomplete")
        if self.generated_at.tzinfo is None or self.generated_at.utcoffset() is None:
            raise ValueError("Strategy evidence timestamp must be timezone-aware")


@dataclass(frozen=True, slots=True)
class SessionEvaluation:
    session: str
    allowed: bool
    reason: str
    timestamp: datetime
    timezone: str
    provenance: str
    state: StrategyEvidenceState

    def __post_init__(self) -> None:
        if not self.session or not self.reason or not self.timezone or not self.provenance:
            raise ValueError("Session evaluation provenance is incomplete")
        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() is None:
            raise ValueError("Session evaluation timestamp must be timezone-aware")


class SessionProvider(Protocol):
    def evaluate(self, *, timestamp: datetime) -> SessionEvaluation: ...


@dataclass(frozen=True, slots=True)
class TradingWindow:
    session: str
    timezone: str
    start: time
    end: time


class ConfiguredSessionProvider:
    """Authoritative server-clock session calendar; browser time is never used."""

    def __init__(self, windows: tuple[TradingWindow, ...] | None = None) -> None:
        self.windows = windows or (
            TradingWindow("LONDON", "Europe/London", time(8), time(17)),
            TradingWindow("NEW_YORK", "America/New_York", time(8), time(17)),
        )
        for window in self.windows:
            try:
                ZoneInfo(window.timezone)
            except ZoneInfoNotFoundError as error:
                raise ValueError("Configured session timezone is unavailable") from error

    def evaluate(self, *, timestamp: datetime) -> SessionEvaluation:
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("Session timestamp must be timezone-aware")
        timestamp = timestamp.astimezone(UTC)
        if timestamp.weekday() >= 5:
            return SessionEvaluation(
                session="WEEKEND", allowed=False, reason="Weekend trading is restricted",
                timestamp=timestamp, timezone="UTC", provenance="configured_session_calendar",
                state=StrategyEvidenceState.SESSION_BLOCKED,
            )
        for window in self.windows:
            local = timestamp.astimezone(ZoneInfo(window.timezone))
            current = local.time().replace(tzinfo=None)
            if window.start <= current < window.end:
                return SessionEvaluation(
                    session=window.session, allowed=True, reason="Within configured trading window",
                    timestamp=timestamp, timezone=window.timezone,
                    provenance=f"configured_session_calendar:{window.session}",
                    state=StrategyEvidenceState.SIGNAL_READY,
                )
        return SessionEvaluation(
            session="OUTSIDE_WINDOW", allowed=False, reason="Outside configured trading windows",
            timestamp=timestamp, timezone="UTC", provenance="configured_session_calendar",
            state=StrategyEvidenceState.SESSION_BLOCKED,
        )


@dataclass(frozen=True, slots=True)
class NewsEvaluation:
    clear: bool
    state: StrategyEvidenceState
    reason: str
    timestamp: datetime
    events: tuple["NewsEvent", ...]
    provenance: str

    def __post_init__(self) -> None:
        if not self.reason or not self.provenance:
            raise ValueError("News evaluation provenance is incomplete")
        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() is None:
            raise ValueError("News evaluation timestamp must be timezone-aware")


class NewsProvider(Protocol):
    async def evaluate(self, *, asset: str, timestamp: datetime) -> NewsEvaluation: ...


@dataclass(frozen=True, slots=True)
class NewsEvent:
    event: str
    timestamp: datetime
    affected_market: str
    impact: str
    source: str
    provenance: str

    def __post_init__(self) -> None:
        if not all((self.event, self.affected_market, self.impact, self.source, self.provenance)):
            raise ValueError("News event provenance is incomplete")
        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() is None:
            raise ValueError("News event timestamp must be timezone-aware")


class NotConfiguredNewsProvider:
    async def evaluate(self, *, asset: str, timestamp: datetime) -> NewsEvaluation:
        del asset
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("News timestamp must be timezone-aware")
        timestamp = timestamp.astimezone(UTC)
        return NewsEvaluation(
            clear=False, state=StrategyEvidenceState.NOT_CONFIGURED,
            reason="No approved high-impact-news provider is configured",
            timestamp=timestamp, events=(), provenance="news_provider:not_configured",
        )
