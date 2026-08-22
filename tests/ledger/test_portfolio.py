import pytest

from app.database.models import Tenant, User, Workspace, WorkspaceMembership
from app.services.portfolio import PortfolioService
from tests.database.helpers import sqlite_session


@pytest.mark.asyncio
async def test_portfolio_is_created_inside_owner_workspace_only():
    async with sqlite_session() as session:
        owner = User(email="owner@example.com", hashed_password="x", first_name="Owner", last_name="User", is_verified=True)
        other = User(email="other@example.com", hashed_password="x", first_name="Other", last_name="User", is_verified=True)
        first = Workspace(name="First", tenant=Tenant(name="First tenant"), owner=owner)
        second = Workspace(name="Second", tenant=Tenant(name="Second tenant"), owner=other)
        session.add_all([owner, other, first, second])
        await session.flush()
        summary = await PortfolioService(session).summary(user_id=owner.id, actor_id=owner.id)
        assert summary.workspace_id == first.id
        assert summary.user_id == owner.id
        with pytest.raises(LookupError):
            await PortfolioService(session).summary(user_id=owner.id, actor_id=owner.id, workspace_id=second.id)


@pytest.mark.asyncio
async def test_portfolio_rejects_cross_account_inspection_without_founder_authorization():
    async with sqlite_session() as session:
        owner = User(email="portfolio-owner@example.com", hashed_password="x", first_name="Owner", last_name="User", is_verified=True)
        other = User(email="portfolio-other@example.com", hashed_password="x", first_name="Other", last_name="User", is_verified=True)
        workspace = Workspace(name="Owner workspace", owner=owner)
        session.add_all([owner, other, workspace])
        await session.flush()
        with pytest.raises(LookupError, match="not authorized"):
            await PortfolioService(session).summary(user_id=owner.id, actor_id=other.id, workspace_id=workspace.id)


@pytest.mark.asyncio
async def test_portfolio_rejects_ambiguous_default_workspace_selection():
    async with sqlite_session() as session:
        owner = User(email="multi-workspace@example.com", hashed_password="x", first_name="Multi", last_name="Workspace", is_verified=True)
        member_owner = User(email="member-owner@example.com", hashed_password="x", first_name="Member", last_name="Owner", is_verified=True)
        first = Workspace(name="First", tenant=Tenant(name="First tenant"), owner=owner)
        second = Workspace(name="Second", tenant=Tenant(name="Second tenant"), owner=member_owner)
        session.add_all([owner, member_owner, first, second, WorkspaceMembership(workspace=second, user=owner, role="member")])
        await session.flush()
        with pytest.raises(LookupError, match="ambiguous"):
            await PortfolioService(session).summary(user_id=owner.id, actor_id=owner.id)
