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

    async def get_canonical_for_user(self, user_id: UUID) -> Workspace | None:
        """Return the sole workspace owned or authorized for a user."""
        workspaces = list(
            (
                await self.session.scalars(
                    select(Workspace)
                    .outerjoin(
                        WorkspaceMembership,
                        WorkspaceMembership.workspace_id == Workspace.id,
                    )
                    .where(
                        (Workspace.owner_id == user_id)
                        | (WorkspaceMembership.user_id == user_id)
                    )
                    .options(selectinload(Workspace.memberships))
                    .order_by(Workspace.created_at, Workspace.id)
                    .distinct()
                )
            ).all()
        )
        return workspaces[0] if len(workspaces) == 1 else None


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
