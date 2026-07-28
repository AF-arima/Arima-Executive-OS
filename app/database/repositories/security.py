from datetime import datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    RateLimitBucket,
    SecurityEvent,
    SecurityToken,
    SecurityTokenPurpose,
)
from app.database.repositories.base import AsyncRepository


class SecurityTokenRepository(AsyncRepository[SecurityToken]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(SecurityToken, session)

    async def invalidate_active(
        self,
        user_id: UUID,
        purpose: SecurityTokenPurpose,
        *,
        now: datetime,
    ) -> None:
        await self.session.execute(
            update(SecurityToken)
            .where(
                SecurityToken.user_id == user_id,
                SecurityToken.purpose == purpose,
                SecurityToken.consumed_at.is_(None),
                SecurityToken.invalidated_at.is_(None),
            )
            .values(invalidated_at=now)
        )

    async def get_active_for_consumption(
        self,
        token_hash: str,
        purpose: SecurityTokenPurpose,
        *,
        now: datetime,
    ) -> SecurityToken | None:
        return await self.session.scalar(
            select(SecurityToken)
            .where(
                SecurityToken.token_hash == token_hash,
                SecurityToken.purpose == purpose,
                SecurityToken.consumed_at.is_(None),
                SecurityToken.invalidated_at.is_(None),
                SecurityToken.expires_at > now,
            )
            .with_for_update()
        )


class SecurityEventRepository(AsyncRepository[SecurityEvent]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(SecurityEvent, session)


class RateLimitRepository(AsyncRepository[RateLimitBucket]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(RateLimitBucket, session)

    async def increment(
        self,
        *,
        scope: str,
        key: str,
        window_started_at: datetime,
    ) -> int:
        bucket = await self.session.scalar(
            select(RateLimitBucket)
            .where(
                RateLimitBucket.scope == scope,
                RateLimitBucket.key == key,
                RateLimitBucket.window_started_at == window_started_at,
            )
            .with_for_update()
        )
        if bucket is not None:
            bucket.count += 1
            await self.session.flush()
            return bucket.count

        bucket = RateLimitBucket(
            scope=scope,
            key=key,
            window_started_at=window_started_at,
            count=1,
        )
        try:
            async with self.session.begin_nested():
                self.session.add(bucket)
                await self.session.flush()
        except IntegrityError:
            bucket = await self.session.scalar(
                select(RateLimitBucket)
                .where(
                    RateLimitBucket.scope == scope,
                    RateLimitBucket.key == key,
                    RateLimitBucket.window_started_at == window_started_at,
                )
                .with_for_update()
            )
            if bucket is None:
                raise
            bucket.count += 1
            await self.session.flush()
        return bucket.count
