from datetime import datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import RefreshTokenSession
from app.database.repositories.base import AsyncRepository


class RefreshTokenRepository(AsyncRepository[RefreshTokenSession]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(RefreshTokenSession, session)

    async def get_by_jti(self, jti: UUID) -> RefreshTokenSession | None:
        return await self.session.scalar(
            select(RefreshTokenSession).where(
                RefreshTokenSession.token_jti == str(jti)
            )
        )

    async def revoke_active(
        self,
        jti: UUID,
        *,
        revoked_at: datetime,
    ) -> UUID | None:
        statement = (
            update(RefreshTokenSession)
            .where(
                RefreshTokenSession.token_jti == str(jti),
                RefreshTokenSession.revoked_at.is_(None),
                RefreshTokenSession.expires_at > revoked_at,
            )
            .values(revoked_at=revoked_at)
            .returning(RefreshTokenSession.user_id)
        )
        return await self.session.scalar(statement)

    async def consume_active(
        self,
        jti: UUID,
        *,
        revoked_at: datetime,
    ) -> RefreshTokenSession | None:
        token = await self.session.scalar(
            select(RefreshTokenSession)
            .where(
                RefreshTokenSession.token_jti == str(jti),
                RefreshTokenSession.revoked_at.is_(None),
                RefreshTokenSession.expires_at > revoked_at,
            )
            .with_for_update()
        )
        if token is None:
            return None
        token.revoked_at = revoked_at
        token.revoked_reason = "rotated"
        token.last_used_at = revoked_at
        await self.session.flush()
        return token

    async def revoke_family(
        self,
        family_id: UUID,
        *,
        revoked_at: datetime,
        reason: str,
    ) -> int:
        result = await self.session.execute(
            update(RefreshTokenSession)
            .where(
                RefreshTokenSession.family_id == family_id,
                RefreshTokenSession.revoked_at.is_(None),
            )
            .values(revoked_at=revoked_at, revoked_reason=reason)
        )
        return int(getattr(result, "rowcount", 0) or 0)

    async def revoke_all_for_user(
        self,
        user_id: UUID,
        *,
        revoked_at: datetime,
        reason: str,
    ) -> int:
        result = await self.session.execute(
            update(RefreshTokenSession)
            .where(
                RefreshTokenSession.user_id == user_id,
                RefreshTokenSession.revoked_at.is_(None),
            )
            .values(revoked_at=revoked_at, revoked_reason=reason)
        )
        return int(getattr(result, "rowcount", 0) or 0)

    async def has_active_family(self, family_id: UUID, *, now: datetime) -> bool:
        value = await self.session.scalar(
            select(RefreshTokenSession.id).where(
                RefreshTokenSession.family_id == family_id,
                RefreshTokenSession.revoked_at.is_(None),
                RefreshTokenSession.expires_at > now,
            )
        )
        return value is not None

    async def list_active_for_user(
        self,
        user_id: UUID,
        *,
        now: datetime,
    ) -> list[RefreshTokenSession]:
        result = await self.session.scalars(
            select(RefreshTokenSession)
            .where(
                RefreshTokenSession.user_id == user_id,
                RefreshTokenSession.revoked_at.is_(None),
                RefreshTokenSession.expires_at > now,
            )
            .order_by(RefreshTokenSession.created_at.desc())
        )
        return list(result.all())
