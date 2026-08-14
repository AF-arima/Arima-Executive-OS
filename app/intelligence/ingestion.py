from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from urllib.parse import parse_qsl, urlsplit
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeDocumentStatus,
    KnowledgeSource,
    User,
)
from app.intelligence.access import (
    IntelligenceAccessError,
    require_workspace_membership,
)
from app.intelligence.schemas import (
    IngestedKnowledge,
    KnowledgeDocumentInput,
    KnowledgeSourceInput,
)


class KnowledgeValidationError(ValueError):
    pass


CREDENTIAL_MARKERS = (
    "secret",
    "token",
    "password",
    "api_key",
    "apikey",
    "credential",
    "authorization",
)
CREDENTIAL_QUERY_MARKERS = (*CREDENTIAL_MARKERS, "key", "auth", "signature")


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class KnowledgeIngestionService:
    """Normalizes workspace content into durable, provenance-bearing chunks."""

    def __init__(self, session: AsyncSession, *, chunk_size: int = 2_000) -> None:
        if chunk_size < 1:
            raise ValueError("chunk_size must be positive")
        self.session = session
        self.chunk_size = chunk_size

    async def create_source(
        self,
        *,
        workspace_id: UUID,
        actor: User,
        data: KnowledgeSourceInput,
    ) -> KnowledgeSource:
        await require_workspace_membership(self.session, actor, workspace_id)
        self._validate_source(data)
        if data.freshness_required and data.max_age_seconds is None:
            raise KnowledgeValidationError("Fresh sources require a maximum age")
        source = await self.session.scalar(
            select(KnowledgeSource).where(
                KnowledgeSource.workspace_id == workspace_id,
                KnowledgeSource.source_type == data.source_type,
                KnowledgeSource.external_id == data.external_id,
            )
        )
        if source is None:
            source = KnowledgeSource(
                workspace_id=workspace_id,
                **data.model_dump(),
            )
            self.session.add(source)
            await self.session.commit()
        return source

    async def ingest(
        self,
        *,
        workspace_id: UUID,
        source_id: UUID,
        actor: User,
        data: KnowledgeDocumentInput,
    ) -> IngestedKnowledge:
        await require_workspace_membership(self.session, actor, workspace_id)
        source = await self.session.scalar(
            select(KnowledgeSource).where(
                KnowledgeSource.id == source_id,
                KnowledgeSource.workspace_id == workspace_id,
                KnowledgeSource.is_enabled.is_(True),
            )
        )
        if source is None:
            raise IntelligenceAccessError("Knowledge source is unavailable")
        self._validate_document(data)
        observed_at = _as_utc(data.source_observed_at)
        expires_at = _as_utc(data.expires_at) if data.expires_at is not None else None
        if source.freshness_required and source.max_age_seconds is not None:
            policy_expiry = observed_at + timedelta(seconds=source.max_age_seconds)
            expires_at = min(expires_at, policy_expiry) if expires_at else policy_expiry

        content_hash = sha256(data.content.encode()).hexdigest()
        document = await self.session.scalar(
            select(KnowledgeDocument).where(
                KnowledgeDocument.source_id == source.id,
                KnowledgeDocument.external_id == data.external_id,
                KnowledgeDocument.content_hash == content_hash,
            )
        )
        if document is None:
            document = KnowledgeDocument(
                workspace_id=workspace_id,
                source_id=source.id,
                external_id=data.external_id,
                title=data.title,
                content_hash=content_hash,
                source_observed_at=observed_at,
                expires_at=expires_at,
                provenance=data.provenance,
                status=KnowledgeDocumentStatus.INGESTED,
            )
            self.session.add(document)
            await self.session.flush()
            for ordinal, content in enumerate(self._chunks(data.content)):
                self.session.add(
                    KnowledgeChunk(
                        workspace_id=workspace_id,
                        document_id=document.id,
                        ordinal=ordinal,
                        content=content,
                        content_hash=sha256(content.encode()).hexdigest(),
                    )
                )
            await self.session.commit()

        chunk_ids = tuple(
            (
                await self.session.scalars(
                    select(KnowledgeChunk.id)
                    .where(KnowledgeChunk.document_id == document.id)
                    .order_by(KnowledgeChunk.ordinal)
                )
            ).all()
        )
        return IngestedKnowledge(
            source_id=source.id,
            document_id=document.id,
            chunk_ids=chunk_ids,
            content_hash=content_hash,
        )

    @staticmethod
    def _validate_source(data: KnowledgeSourceInput) -> None:
        if data.source_uri is None:
            return
        if KnowledgeIngestionService._contains_credentials(data.source_uri):
            raise KnowledgeValidationError(
                "Source URI cannot contain provider credentials"
            )

    @staticmethod
    def _validate_document(data: KnowledgeDocumentInput) -> None:
        provenance_identifiers = [
            data.provenance.get(key)
            for key in ("source", "origin", "provider")
            if key in data.provenance
        ]
        if not data.provenance or not any(
            isinstance(value, str) and value.strip() for value in provenance_identifiers
        ):
            raise KnowledgeValidationError("Source provenance is required")
        if KnowledgeIngestionService._contains_credentials(data.provenance):
            raise KnowledgeValidationError(
                "Provider credentials cannot be stored as provenance"
            )
        observed_at = _as_utc(data.source_observed_at)
        if observed_at > datetime.now(UTC) + timedelta(minutes=5):
            raise KnowledgeValidationError("Source observation is in the future")
        if data.expires_at is not None and _as_utc(data.expires_at) <= observed_at:
            raise KnowledgeValidationError("Source expiry must follow observation")

    def _chunks(self, content: str) -> list[str]:
        return [
            content[start : start + self.chunk_size]
            for start in range(0, len(content), self.chunk_size)
        ]

    @staticmethod
    def _contains_credentials(value: object) -> bool:
        if isinstance(value, dict):
            return any(
                any(marker in str(key).casefold() for marker in CREDENTIAL_MARKERS)
                or KnowledgeIngestionService._contains_credentials(item)
                for key, item in value.items()
            )
        if isinstance(value, (list, tuple, set)):
            return any(
                KnowledgeIngestionService._contains_credentials(item) for item in value
            )
        if not isinstance(value, str):
            return False
        parsed = urlsplit(value)
        if parsed.username is not None or parsed.password is not None:
            return True
        parameters = (
            *parse_qsl(parsed.query, keep_blank_values=True),
            *parse_qsl(parsed.fragment, keep_blank_values=True),
        )
        if any(
            marker in key.casefold()
            for key, _ in parameters
            for marker in CREDENTIAL_QUERY_MARKERS
        ):
            return True
        normalized = value.casefold()
        return any(
            f"{marker}=" in normalized or f"{marker}:" in normalized
            for marker in CREDENTIAL_MARKERS
        )
