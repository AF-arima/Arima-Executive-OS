from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Tenant, User, Workspace, WorkspaceMembership
from app.services.permissions import has_founder_control_access


class FinancialContextError(RuntimeError):
    """A financial context could not be proven from authenticated state."""


@dataclass(frozen=True, slots=True)
class AuthorizedFinancialContext:
    actor_id: UUID
    tenant_id: UUID
    workspace_id: UUID
    account_id: UUID


class FinancialContextResolver:
    """Resolves the canonical actor → tenant → workspace → account chain."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def resolve(
        self,
        *,
        actor: User,
        workspace_id: UUID,
        account_id: UUID,
        tenant_id: UUID | None = None,
    ) -> AuthorizedFinancialContext:
        workspace = await self.session.scalar(
            select(Workspace).where(Workspace.id == workspace_id)
        )
        if workspace is None or workspace.tenant_id is None:
            raise FinancialContextError("Authorized tenant/workspace relationship is unavailable")
        tenant = await self.session.get(Tenant, workspace.tenant_id)
        if tenant is None:
            raise FinancialContextError("Authorized tenant is unavailable")
        if tenant_id is not None and tenant.id != tenant_id:
            raise FinancialContextError("Tenant context does not match the authorized workspace")

        actor_membership = await self.session.scalar(
            select(WorkspaceMembership).where(
                WorkspaceMembership.workspace_id == workspace_id,
                WorkspaceMembership.user_id == actor.id,
            )
        )
        if workspace.owner_id != actor.id and actor_membership is None and not has_founder_control_access(actor):
            raise FinancialContextError("Actor is not authorized for the workspace")

        account = await self.session.get(User, account_id)
        if account is None:
            raise FinancialContextError("Financial account owner is unavailable")
        account_membership = await self.session.scalar(
            select(WorkspaceMembership).where(
                WorkspaceMembership.workspace_id == workspace_id,
                WorkspaceMembership.user_id == account_id,
            )
        )
        if workspace.owner_id != account_id and account_membership is None:
            raise FinancialContextError("Account is not authorized for the workspace")
        if account_id != actor.id and not has_founder_control_access(actor):
            raise FinancialContextError("Actor is not authorized for the financial account")

        return AuthorizedFinancialContext(
            actor_id=actor.id,
            tenant_id=tenant.id,
            workspace_id=workspace.id,
            account_id=account.id,
        )


class DatabaseFinancialContextAuthorizer:
    """Concrete authorizer used by risk/QTrade boundaries."""

    def __init__(self, session: AsyncSession, actor: User) -> None:
        self.session = session
        self.actor = actor
        self.resolver = FinancialContextResolver(session)

    async def authorize(
        self,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        actor_id: UUID,
        account_id: UUID,
    ) -> None:
        if actor_id != self.actor.id:
            raise FinancialContextError("Authenticated actor does not match execution actor")
        context = await self.resolver.resolve(
            actor=self.actor,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            account_id=account_id,
        )
        if context.actor_id != actor_id:
            raise FinancialContextError("Financial context actor mismatch")
