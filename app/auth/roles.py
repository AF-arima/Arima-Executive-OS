from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Role

DEFAULT_ROLES: dict[str, str] = {
    "administrator": "Full administrative access",
    "executive": "Executive-level access",
    "manager": "Team and project management access",
    "analyst": "Analysis and reporting access",
    "viewer": "Read-only access",
}
DEFAULT_USER_ROLE = "viewer"


async def seed_default_roles(session: AsyncSession) -> dict[str, Role]:
    result = await session.scalars(
        select(Role).where(Role.name.in_(DEFAULT_ROLES))
    )
    roles = {role.name: role for role in result.all()}

    for name, description in DEFAULT_ROLES.items():
        if name not in roles:
            role = Role(name=name, description=description)
            try:
                async with session.begin_nested():
                    session.add(role)
                    await session.flush()
            except IntegrityError:
                existing = await session.scalar(
                    select(Role).where(Role.name == name)
                )
                if existing is None:
                    raise
                role = existing
            roles[name] = role

    await session.flush()
    return roles
