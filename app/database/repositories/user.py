from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.models import User
from app.database.repositories.base import AsyncRepository


class UserRepository(AsyncRepository[User]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(User, session)

    async def get_by_email(self, email: str) -> User | None:
        return await self.session.scalar(
            select(User).where(func.lower(User.email) == email.strip().lower())
        )

    async def get_with_roles(self, user_id: UUID) -> User | None:
        return await self.session.scalar(
            select(User)
            .where(User.id == user_id)
            .options(
                selectinload(User.roles),
                selectinload(User.owned_workspace),
            )
            .execution_options(populate_existing=True)
        )
