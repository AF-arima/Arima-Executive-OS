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
