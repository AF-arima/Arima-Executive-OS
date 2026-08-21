from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from app.quant.contracts import ResearchSignal


@dataclass(frozen=True, slots=True)
class RiskSnapshot:
    """Authoritative risk state for one tenant/workspace/account context.

    ``None`` means unknown and is rejected by :class:`RiskEngine`; an explicit
    Decimal zero or empty mapping means the source established no exposure.
    Concentration is an asset value divided by total equity, using the same
    approved valuation basis as the snapshot. It is not a raw quantity.
    """
    tenant_id: UUID | None
    workspace_id: UUID
    account_id: UUID
    actor_id: UUID | None
    total_equity: Decimal
    available_capital: Decimal
    reserved_capital: Decimal
    current_exposure: Decimal | None
    asset_exposure: dict[str, Decimal] = field(default_factory=dict)
    concentration: dict[str, Decimal] | None = None
    realized_pnl: Decimal | None = None
    unrealized_pnl: Decimal | None = None
    daily_loss: Decimal | None = None
    strategy_exposure: dict[str, Decimal] | None = None
    provenance: "RiskProvenance | None" = None
    _trusted: bool = field(default=False, init=False, repr=False, compare=False)

@dataclass(frozen=True, slots=True)
class RiskProvenance:
    source: str
    calculated_at: datetime
    input_names: frozenset[str]
    valuation_source: str | None = None
    freshness_seconds: int | None = None
    tenant_id: UUID | None = None
    workspace_id: UUID | None = None
    account_id: UUID | None = None
    actor_id: UUID | None = None

    def __post_init__(self) -> None:
        if not self.source:
            raise ValueError("Risk provenance requires a source")
        if self.calculated_at.tzinfo is None or self.calculated_at.utcoffset() is None:
            raise ValueError("Risk provenance timestamp must be timezone-aware")
        if self.freshness_seconds is not None and self.freshness_seconds < 0:
            raise ValueError("Risk provenance freshness must not be negative")


@dataclass(frozen=True, slots=True)
class RiskAuthority:
    """Opaque capability held only by a trusted risk-input service."""

    _marker: object


_RISK_AUTHORITY_MARKER = object()


def _issue_risk_authority() -> RiskAuthority:
    return RiskAuthority(_RISK_AUTHORITY_MARKER)


@dataclass(frozen=True, slots=True)
class AuthoritativeRiskInputs:
    tenant_id: UUID
    workspace_id: UUID
    account_id: UUID
    actor_id: UUID
    total_equity: Decimal
    available_capital: Decimal
    reserved_capital: Decimal
    current_exposure: Decimal | None
    asset_exposure: dict[str, Decimal]
    concentration: dict[str, Decimal] | None
    realized_pnl: Decimal | None
    unrealized_pnl: Decimal | None
    daily_loss: Decimal | None
    strategy_exposure: dict[str, Decimal] | None
    provenance: RiskProvenance


class TrustedRiskSnapshotBuilder:
    """Only trusted server-side providers receive the authority capability."""

    def __init__(self, authority: RiskAuthority) -> None:
        if authority._marker is not _RISK_AUTHORITY_MARKER:
            raise RiskSnapshotAuthorityError("Invalid risk snapshot authority")
        self._authority = authority

    def build(self, inputs: AuthoritativeRiskInputs) -> RiskSnapshot:
        provenance = inputs.provenance
        supplied_context = (provenance.tenant_id, provenance.workspace_id, provenance.account_id, provenance.actor_id)
        if any(value is not None for value in supplied_context) and supplied_context != (
            inputs.tenant_id, inputs.workspace_id, inputs.account_id, inputs.actor_id
        ):
            raise RiskValidationError("Risk provenance identity does not match authoritative inputs")
        provenance = RiskProvenance(
            source=provenance.source, calculated_at=provenance.calculated_at,
            input_names=provenance.input_names, valuation_source=provenance.valuation_source,
            freshness_seconds=provenance.freshness_seconds, tenant_id=inputs.tenant_id,
            workspace_id=inputs.workspace_id, account_id=inputs.account_id, actor_id=inputs.actor_id,
        )
        if provenance.calculated_at > datetime.now(UTC):
            raise RiskValidationError("Risk provenance cannot be from the future")
        if provenance.freshness_seconds is not None:
            age = (datetime.now(UTC) - provenance.calculated_at).total_seconds()
            if age > provenance.freshness_seconds:
                raise RiskInputUnavailableError("stale_risk_inputs")
        if provenance.valuation_source is None and (
            len(inputs.asset_exposure) > 1 or inputs.concentration
        ):
            raise ValuationUnavailableError("Valuation provenance is required for multi-asset risk")
        snapshot = RiskSnapshot(
            tenant_id=inputs.tenant_id, workspace_id=inputs.workspace_id,
            account_id=inputs.account_id, actor_id=inputs.actor_id,
            total_equity=inputs.total_equity, available_capital=inputs.available_capital,
            reserved_capital=inputs.reserved_capital, current_exposure=inputs.current_exposure,
            asset_exposure=dict(inputs.asset_exposure), concentration=(dict(inputs.concentration) if inputs.concentration is not None else None),
            realized_pnl=inputs.realized_pnl, unrealized_pnl=inputs.unrealized_pnl,
            daily_loss=inputs.daily_loss, strategy_exposure=(dict(inputs.strategy_exposure) if inputs.strategy_exposure is not None else None),
            provenance=provenance,
        )
        object.__setattr__(snapshot, "_trusted", True)
        return snapshot


class RiskProvider(Protocol):
    async def snapshot(self, *, tenant_id: UUID, workspace_id: UUID, actor_id: UUID, account_id: UUID) -> RiskSnapshot: ...


class FinancialContextAuthorizer(Protocol):
    async def authorize(self, *, tenant_id: UUID, workspace_id: UUID, actor_id: UUID, account_id: UUID) -> None: ...


class ValuationProvider(Protocol):
    async def value(self, *, asset: str, amount: Decimal) -> Decimal: ...


@dataclass(frozen=True, slots=True)
class DailyLossSemantics:
    """Required accounting semantics for an authoritative daily-loss source."""

    timezone: str
    reset_boundary: str
    includes_realized_pnl: bool
    includes_unrealized_pnl: bool
    includes_deposits: bool
    includes_withdrawals: bool
    includes_transfers: bool
    includes_fees: bool
    includes_open_positions: bool
    corporate_actions_policy: str
    max_age_seconds: int


@dataclass(frozen=True, slots=True)
class StrategyExposureSemantics:
    strategy_identity: str
    includes_positions: bool
    includes_orders: bool
    includes_reserved_orders: bool
    aggregation_boundary: str
    max_age_seconds: int


@dataclass(frozen=True, slots=True)
class ValuationObservation:
    asset: str
    quantity: Decimal
    value: Decimal
    currency: str
    source: str
    observed_at: datetime
    precision: int
    max_age_seconds: int

    def validate_freshness(self, *, now: datetime | None = None) -> None:
        current = now or datetime.now(UTC)
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValuationUnavailableError("Valuation timestamp must be timezone-aware")
        if current - self.observed_at > timedelta(seconds=self.max_age_seconds):
            raise ValuationUnavailableError("Valuation is stale")
        if self.value < 0 or self.precision < 0 or not self.source:
            raise ValuationUnavailableError("Valuation observation is invalid")


@dataclass(frozen=True, slots=True)
class RiskInputAvailability:
    name: str
    value: Decimal | dict[str, Decimal] | None
    source: str | None
    observed_at: datetime | None

    @property
    def available(self) -> bool:
        return self.value is not None and bool(self.source) and self.observed_at is not None


class ValuationUnavailableError(RuntimeError):
    pass


class CircuitStateUnavailableError(RuntimeError):
    pass


class RiskValidationError(RuntimeError):
    pass


class RiskSnapshotAuthorityError(RiskValidationError):
    pass


class RiskInputUnavailableError(RiskValidationError):
    """A required risk dimension is unknown, not legitimately zero."""

    def __init__(self, input_name: str) -> None:
        self.input_name = input_name
        super().__init__(f"Authoritative risk input unavailable: {input_name}")


@dataclass(frozen=True, slots=True)
class RiskExecutionContext:
    tenant_id: UUID
    workspace_id: UUID
    actor_id: UUID
    account_id: UUID


class PortfolioRiskProvider:
    """Contract adapter; pricing and daily-loss sources remain explicit inputs."""

    def __init__(self, portfolio_service, valuation_provider: ValuationProvider | None = None) -> None:
        self.portfolio_service = portfolio_service
        self.valuation_provider = valuation_provider
        self._builder = TrustedRiskSnapshotBuilder(_issue_risk_authority())

    async def snapshot(self, *, tenant_id: UUID, workspace_id: UUID, actor_id: UUID, account_id: UUID) -> RiskSnapshot:
        summary = await self.portfolio_service.summary(user_id=account_id, workspace_id=workspace_id, actor_id=actor_id, require_authoritative_context=True)
        if summary.workspace_id != workspace_id or summary.user_id != account_id:
            raise RiskValidationError("Portfolio risk context does not match the authorized account")
        if len(summary.balances) > 1 and self.valuation_provider is None:
            raise ValuationUnavailableError("Multi-asset equity requires an authorized valuation provider")
        if self.valuation_provider is None and summary.positions:
            raise ValuationUnavailableError("Position valuation requires an authorized valuation provider")
        # No authoritative daily-loss or strategy-exposure source exists in the
        # repository. Never substitute zero for either missing dimension.
        raise RiskInputUnavailableError("daily_loss")


@dataclass(frozen=True, slots=True)
class RiskLimits:
    maximum_risk: Decimal
    maximum_concentration: Decimal = Decimal("1")
    maximum_daily_loss: Decimal | None = None
    maximum_exposure: Decimal | None = None
    maximum_strategy_exposure: Decimal | None = None


@dataclass(frozen=True, slots=True)
class RiskDecision:
    allowed: bool
    reason: str
    position_size: Decimal
    risk_amount: Decimal


class RiskEngine:
    """Fail-closed validation boundary between QLab and QTrade."""

    REQUIRED_INPUTS = frozenset({
        "current_exposure", "concentration", "realized_pnl",
        "unrealized_pnl", "daily_loss", "strategy_exposure",
    })

    def validate(
        self, *, signal: ResearchSignal, snapshot: RiskSnapshot,
        limits: RiskLimits, context: RiskExecutionContext,
    ) -> RiskDecision:
        if not snapshot._trusted or snapshot.provenance is None:
            raise RiskInputUnavailableError("risk_provenance")
        if (
            snapshot.provenance.tenant_id != snapshot.tenant_id
            or snapshot.provenance.workspace_id != snapshot.workspace_id
            or snapshot.provenance.account_id != snapshot.account_id
            or snapshot.provenance.actor_id != snapshot.actor_id
        ):
            raise RiskInputUnavailableError("risk_provenance_identity")
        if snapshot.provenance.freshness_seconds is not None:
            age = (datetime.now(UTC) - snapshot.provenance.calculated_at).total_seconds()
            if age > snapshot.provenance.freshness_seconds:
                raise RiskInputUnavailableError("stale_risk_inputs")
        missing_provenance = self.REQUIRED_INPUTS.difference(snapshot.provenance.input_names)
        if missing_provenance:
            raise RiskInputUnavailableError(f"risk_provenance:{sorted(missing_provenance)[0]}")
        if (
            signal.tenant_id is None or signal.actor_id is None
            or snapshot.tenant_id is None or snapshot.actor_id is None
            or context.tenant_id != signal.tenant_id
            or context.workspace_id != signal.workspace_id
            or context.account_id != signal.account_id
            or context.actor_id != signal.actor_id
            or snapshot.tenant_id != context.tenant_id
            or snapshot.workspace_id != context.workspace_id
            or snapshot.account_id != context.account_id
            or snapshot.actor_id != context.actor_id
        ):
            raise RiskValidationError("Risk execution identity does not match signal, snapshot, and context")
        required_inputs = {
            "current_exposure": snapshot.current_exposure,
            "concentration": snapshot.concentration,
            "realized_pnl": snapshot.realized_pnl,
            "unrealized_pnl": snapshot.unrealized_pnl,
            "daily_loss": snapshot.daily_loss,
            "strategy_exposure": snapshot.strategy_exposure,
        }
        for input_name, value in required_inputs.items():
            if value is None:
                raise RiskInputUnavailableError(input_name)
        if snapshot.total_equity <= 0 or snapshot.available_capital < 0 or snapshot.reserved_capital < 0:
            raise RiskValidationError("Authoritative capital is unavailable or invalid")
        if snapshot.available_capital + snapshot.reserved_capital > snapshot.total_equity:
            raise RiskValidationError("Authoritative capital buckets are inconsistent")
        if limits.maximum_risk <= 0:
            raise RiskValidationError("Maximum allowed risk is not configured")
        if signal.asset not in snapshot.concentration:
            raise RiskInputUnavailableError(f"concentration:{signal.asset}")
        concentration = snapshot.concentration[signal.asset]
        if concentration > limits.maximum_concentration:
            return RiskDecision(False, "asset concentration limit exceeded", Decimal("0"), Decimal("0"))
        if limits.maximum_daily_loss is not None and snapshot.daily_loss >= limits.maximum_daily_loss:
            return RiskDecision(False, "daily loss limit exceeded", Decimal("0"), Decimal("0"))
        if limits.maximum_exposure is not None and snapshot.current_exposure >= limits.maximum_exposure:
            return RiskDecision(False, "portfolio exposure limit exceeded", Decimal("0"), Decimal("0"))
        if signal.strategy not in snapshot.strategy_exposure:
            raise RiskInputUnavailableError(f"strategy_exposure:{signal.strategy}")
        if limits.maximum_strategy_exposure is not None and snapshot.strategy_exposure[signal.strategy] >= limits.maximum_strategy_exposure:
            return RiskDecision(False, "strategy exposure limit exceeded", Decimal("0"), Decimal("0"))
        risk_per_unit = abs(signal.entry - signal.stop)
        if risk_per_unit <= 0:
            raise RiskValidationError("Signal stop does not define measurable risk")
        position_size = (limits.maximum_risk / risk_per_unit).quantize(Decimal("0.00000001"))
        if position_size <= 0 or position_size * signal.entry > snapshot.available_capital:
            return RiskDecision(False, "available capital is insufficient", Decimal("0"), Decimal("0"))
        if signal.asset not in snapshot.asset_exposure:
            raise RiskInputUnavailableError(f"asset_exposure:{signal.asset}")
        if snapshot.asset_exposure[signal.asset] < 0:
            raise RiskValidationError("Authoritative exposure is invalid")
        return RiskDecision(True, "risk checks passed", position_size, limits.maximum_risk)
