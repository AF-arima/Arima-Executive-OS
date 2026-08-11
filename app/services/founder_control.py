from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from time import perf_counter
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.database.models import (
    AuditAction,
    AuditEntity,
    DataFeedObservation,
    User,
)
from app.database.repositories import DataFeedObservationRepository
from app.schemas.founder import (
    ConfigurationEnvironment,
    ConfigurationStatus,
    FounderDataFeed,
    FounderDataFeeds,
    FounderFeedError,
    FounderHealthComponent,
    FounderSystemHealth,
    ManualObservationCreate,
    ManualObservationRead,
)
from app.services.audit import record_audit
from app.services.exceptions import ResourceNotFoundError

logger = logging.getLogger(__name__)
UTC = timezone.utc


@dataclass(frozen=True, slots=True)
class FeedDefinition:
    key: str
    label: str
    provider: str | None
    error: FounderFeedError


FEED_CATALOG: tuple[FeedDefinition, ...] = (
    FeedDefinition(
        key="market_data",
        label="Market data",
        provider=None,
        error=FounderFeedError(
            code="unverified_contract",
            message=(
                "No authenticated, versioned market-data feed contract is "
                "configured."
            ),
        ),
    ),
    FeedDefinition(
        key="quant_research",
        label="Quant research",
        provider=None,
        error=FounderFeedError(
            code="not_implemented",
            message="No production Quant Engine or ranking contract is configured.",
        ),
    ),
    FeedDefinition(
        key="portfolio_data",
        label="Portfolio data",
        provider=None,
        error=FounderFeedError(
            code="not_implemented",
            message="No portfolio data model or ingestion contract is configured.",
        ),
    ),
    FeedDefinition(
        key="documents",
        label="Documents",
        provider=None,
        error=FounderFeedError(
            code="not_implemented",
            message="No document storage or retrieval contract is configured.",
        ),
    ),
)
FEEDS_BY_KEY = {feed.key: feed for feed in FEED_CATALOG}


class FounderControlService:
    """Founder-only operational status and provenance service.

    This service never creates business data, estimates metrics, or exposes a
    secret. Manual observations are immutable provenance records associated
    with an audited, server-authorized actor.
    """

    def __init__(
        self,
        session: AsyncSession,
        *,
        settings: Settings | None = None,
    ) -> None:
        self.session = session
        self.repository = DataFeedObservationRepository(session)
        self.settings = settings or get_settings()

    async def system_health(self) -> FounderSystemHealth:
        checked_at = datetime.now(UTC)
        components = [self._backend_component(checked_at)]
        components.append(await self._database_component(checked_at))
        components.append(self._email_component(checked_at))
        components.append(self._voice_component(checked_at))
        return FounderSystemHealth(
            generated_at=checked_at,
            components=components,
        )

    async def data_feeds(self) -> FounderDataFeeds:
        now = datetime.now(UTC)
        latest = await self.repository.latest_for_feed_keys(
            tuple(FEEDS_BY_KEY),
        )
        user_ids = {
            observation.entered_by_id for observation in latest.values()
        }
        authors = await self._author_emails(user_ids)
        return FounderDataFeeds(
            generated_at=now,
            feeds=[
                self._feed_response(
                    definition,
                    latest.get(definition.key),
                    authors,
                    now,
                )
                for definition in FEED_CATALOG
            ],
        )

    async def create_manual_observation(
        self,
        *,
        feed_key: str,
        data: ManualObservationCreate,
        actor: User,
        correlation_id: UUID,
    ) -> ManualObservationRead:
        if feed_key not in FEEDS_BY_KEY:
            raise ResourceNotFoundError("Data feed not found")

        observation = await self.repository.add(
            DataFeedObservation(
                feed_key=feed_key,
                source=data.source,
                observed_at=data.observed_at,
                expires_at=data.expires_at,
                notes=data.notes,
                entered_by_id=actor.id,
                correlation_id=correlation_id,
            )
        )
        record_audit(
            self.session,
            actor_id=actor.id,
            action=AuditAction.CREATE,
            entity=AuditEntity.DATA_FEED_OBSERVATION,
            entity_id=observation.id,
        )
        await self.session.commit()
        await self.session.refresh(observation)
        return self._observation_response(observation, actor.email)

    async def _database_component(
        self,
        checked_at: datetime,
    ) -> FounderHealthComponent:
        started_at = perf_counter()
        try:
            await self.session.execute(text("SELECT 1"))
        except Exception:
            logger.warning("founder_database_health_check_failed", exc_info=True)
            return FounderHealthComponent(
                key="database",
                label="Database",
                status="unavailable",
                checked_at=checked_at,
                latency_ms=None,
                provider="SQLAlchemy",
                configuration=self._not_applicable_configuration(),
                message="The database readiness query failed.",
            )
        return FounderHealthComponent(
            key="database",
            label="Database",
            status="operational",
            checked_at=checked_at,
            latency_ms=round((perf_counter() - started_at) * 1_000, 2),
            provider="SQLAlchemy",
            configuration=self._not_applicable_configuration(),
            message=None,
        )

    def _backend_component(
        self,
        checked_at: datetime,
    ) -> FounderHealthComponent:
        return FounderHealthComponent(
            key="backend",
            label="Backend API",
            status="operational",
            checked_at=checked_at,
            latency_ms=0.0,
            provider="FastAPI",
            configuration=self._not_applicable_configuration(),
            message="The endpoint is responding from the authenticated API.",
        )

    def _email_component(
        self,
        checked_at: datetime,
    ) -> FounderHealthComponent:
        configuration = self._email_configuration()
        configured = configuration.state == "configured"
        return FounderHealthComponent(
            key="email_configuration",
            label="Email configuration",
            status="operational" if configured else "configuration_required",
            checked_at=checked_at,
            latency_ms=None,
            provider=self.settings.email_provider,
            configuration=configuration,
            message=(
                "Configuration is present; message delivery is not probed."
                if configured
                else "Required transactional-email configuration is missing."
            ),
        )

    def _voice_component(
        self,
        checked_at: datetime,
    ) -> FounderHealthComponent:
        return FounderHealthComponent(
            key="voice",
            label="AI voice",
            status="unavailable",
            checked_at=checked_at,
            latency_ms=None,
            provider=None,
            configuration=ConfigurationStatus(
                state="not_configured",
                environment=[
                    ConfigurationEnvironment(
                        name="ARIMA_VOICE_ENABLED",
                        present=self.settings.arima_voice_enabled,
                    ),
                    ConfigurationEnvironment(
                        name="DEFAULT_PROVIDER",
                        present=self.settings.default_provider != "mock",
                    ),
                ],
            ),
            message="No verified non-mock voice provider is enabled.",
        )

    async def _author_emails(self, user_ids: set[UUID]) -> dict[UUID, str]:
        if not user_ids:
            return {}
        users = (
            await self.session.scalars(
                select(User).where(User.id.in_(user_ids))
            )
        ).all()
        return {user.id: user.email for user in users}

    def _feed_response(
        self,
        definition: FeedDefinition,
        observation: DataFeedObservation | None,
        authors: dict[UUID, str],
        now: datetime,
    ) -> FounderDataFeed:
        if observation is None:
            return FounderDataFeed(
                key=definition.key,
                label=definition.label,
                provider=definition.provider,
                status="unavailable",
                freshness="unavailable",
                last_updated_at=None,
                source=None,
                entered_by=None,
                notes=None,
                expires_at=None,
                errors=[definition.error],
                configuration=ConfigurationStatus(
                    state="manual_only",
                    environment=[],
                ),
                manual_entry_supported=True,
            )

        stale = (
            observation.expires_at is not None and observation.expires_at <= now
        )
        return FounderDataFeed(
            key=definition.key,
            label=definition.label,
            provider=definition.provider,
            status="stale" if stale else "manual",
            freshness="stale" if stale else "current",
            last_updated_at=observation.observed_at,
            source=observation.source,
            entered_by=authors.get(observation.entered_by_id),
            notes=observation.notes,
            expires_at=observation.expires_at,
            errors=[definition.error],
            configuration=ConfigurationStatus(
                state="manual_only",
                environment=[],
            ),
            manual_entry_supported=True,
        )

    def _email_configuration(self) -> ConfigurationStatus:
        provider = self.settings.email_provider
        environment = [
            ConfigurationEnvironment(
                name="EMAIL_PROVIDER",
                present=provider is not None,
            ),
            ConfigurationEnvironment(
                name="SMTP_FROM_EMAIL",
                present=self.settings.email_from_address is not None,
            ),
            ConfigurationEnvironment(
                name="SMTP_FROM_NAME",
                present=bool(self.settings.email_from_name.strip()),
            ),
        ]
        if provider == "resend":
            environment.append(
                ConfigurationEnvironment(
                    name="RESEND_API_KEY",
                    present=self._secret_is_present(self.settings.resend_api_key),
                )
            )
            configured = all(item.present for item in environment)
        elif provider == "smtp":
            environment.extend(
                (
                    ConfigurationEnvironment(
                        name="SMTP_HOST",
                        present=bool(self.settings.smtp_host),
                    ),
                    ConfigurationEnvironment(
                        name="SMTP_USERNAME",
                        present=bool(self.settings.smtp_username),
                    ),
                    ConfigurationEnvironment(
                        name="SMTP_PASSWORD",
                        present=self._secret_is_present(
                            self.settings.smtp_password
                        ),
                    ),
                )
            )
            configured = all(item.present for item in environment)
        else:
            configured = False
        return ConfigurationStatus(
            state="configured" if configured else "configuration_required",
            environment=environment,
        )

    @staticmethod
    def _secret_is_present(secret: object | None) -> bool:
        get_secret_value = getattr(secret, "get_secret_value", None)
        return bool(get_secret_value and get_secret_value().strip())

    @staticmethod
    def _not_applicable_configuration() -> ConfigurationStatus:
        return ConfigurationStatus(state="not_applicable", environment=[])

    @staticmethod
    def _observation_response(
        observation: DataFeedObservation,
        entered_by: str,
    ) -> ManualObservationRead:
        return ManualObservationRead(
            id=observation.id,
            feed_key=observation.feed_key,
            source=observation.source,
            observed_at=observation.observed_at,
            notes=observation.notes,
            expires_at=observation.expires_at,
            entered_by=entered_by,
            correlation_id=observation.correlation_id,
            created_at=observation.created_at,
        )
