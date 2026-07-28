from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.models import Workspace, WorkspaceMembership
from app.database.repositories.base import AsyncRepository


class WorkspaceRepository(AsyncRepository[Workspace]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(Workspace, session)

    async def get_by_owner(self, owner_id: UUID) -> Workspace | None:
        return await self.session.scalar(
            select(Workspace)
            .where(Workspace.owner_id == owner_id)
            .options(selectinload(Workspace.memberships))
        )


class WorkspaceMembershipRepository(AsyncRepository[WorkspaceMembership]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(WorkspaceMembership, session)

    async def get_for_user(self, user_id: UUID) -> WorkspaceMembership | None:
        return await self.session.scalar(
            select(WorkspaceMembership)
            .where(WorkspaceMembership.user_id == user_id)
            .options(selectinload(WorkspaceMembership.workspace))
        )

    async def shares_workspace(
        self,
        first_user_id: UUID,
        second_user_id: UUID,
    ) -> bool:
        if first_user_id == second_user_id:
            return True
        first_workspaces = select(WorkspaceMembership.workspace_id).where(
            WorkspaceMembership.user_id == first_user_id
        )
        return (
            await self.session.scalar(
                select(WorkspaceMembership.workspace_id).where(
                    WorkspaceMembership.user_id == second_user_id,
                    WorkspaceMembership.workspace_id.in_(first_workspaces),
                )
            )
            is not None
        )
