"""Provider-neutral quantitative research contracts."""

from app.quant.contracts import MarketRegime, QLabResearchProvider, QLabService, QLabUnavailableError, ResearchProvenance, ResearchSignal
from app.quant.evidence import EvidenceState, OHLCVBar, StructuralEvidence, build_structural_evidence
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

__all__ = ["ConfiguredSessionProvider", "EvidenceState", "MarketRegime", "NewsEvaluation", "NewsEvent", "NotConfiguredNewsProvider", "OHLCVBar", "QLabResearchProvider", "QLabService", "QLabUnavailableError", "ResearchProvenance", "ResearchSignal", "SessionEvaluation", "StrategyEvidenceProvenance", "StrategyEvidenceState", "StructuralEvidence", "TradingWindow", "build_structural_evidence"]
