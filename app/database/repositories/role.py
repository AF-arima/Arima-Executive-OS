from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Role
from app.database.repositories.base import AsyncRepository


class RoleRepository(AsyncRepository[Role]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(Role, session)

    async def get_by_name(self, name: str) -> Role | None:
        return await self.session.scalar(
            select(Role).where(Role.name == name)
        )
