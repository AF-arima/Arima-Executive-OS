from dataclasses import dataclass
from enum import Enum
from uuid import UUID

from app.database.models import AgentConversation, AgentMemory, Project, Task, User

FULL_ACCESS_ROLES = frozenset({"administrator", "executive"})


class VisibilityKind(str, Enum):
    GLOBAL = "global"
    OWNED = "owned"
    ASSIGNED = "assigned"


@dataclass(frozen=True, slots=True)
class AnalyticsScope:
    kind: VisibilityKind
    user_id: UUID
    roles: tuple[str, ...]


def user_roles(user: User) -> frozenset[str]:
    return frozenset(role.name for role in user.roles)


def has_full_access(user: User) -> bool:
    return not FULL_ACCESS_ROLES.isdisjoint(user_roles(user))


def analytics_scope(user: User) -> AnalyticsScope:
    roles = user_roles(user)
    if FULL_ACCESS_ROLES.intersection(roles):
        kind = VisibilityKind.GLOBAL
    elif "manager" in roles:
        kind = VisibilityKind.OWNED
    elif "analyst" in roles:
        kind = VisibilityKind.ASSIGNED
    else:
        kind = VisibilityKind.GLOBAL
    return AnalyticsScope(
        kind=kind,
        user_id=user.id,
        roles=tuple(sorted(roles)),
    )


def workload_scope(user: User) -> AnalyticsScope:
    scope = analytics_scope(user)
    elevated = FULL_ACCESS_ROLES | {"manager", "analyst"}
    if "viewer" in scope.roles and elevated.isdisjoint(scope.roles):
        return AnalyticsScope(
            kind=VisibilityKind.ASSIGNED,
            user_id=user.id,
            roles=scope.roles,
        )
    return scope


def crm_scope(user: User) -> AnalyticsScope:
    roles = user_roles(user)
    if FULL_ACCESS_ROLES.intersection(roles):
        kind = VisibilityKind.GLOBAL
    elif "manager" in roles:
        kind = VisibilityKind.OWNED
    else:
        kind = VisibilityKind.ASSIGNED
    return AnalyticsScope(
        kind=kind,
        user_id=user.id,
        roles=tuple(sorted(roles)),
    )


def can_create_crm(user: User) -> bool:
    roles = user_roles(user)
    return bool(FULL_ACCESS_ROLES.intersection(roles) or "manager" in roles)


def can_contribute_crm(user: User) -> bool:
    return can_create_crm(user) or "analyst" in user_roles(user)


def can_manage_crm_record(
    user: User,
    *,
    owner_id: UUID | None,
    created_by: UUID,
) -> bool:
    if has_full_access(user):
        return True
    roles = user_roles(user)
    if "manager" in roles:
        return owner_id == user.id or created_by == user.id
    return "analyst" in roles and owner_id == user.id


def can_manage_pipelines(user: User) -> bool:
    return has_full_access(user)


def outreach_scope(user: User) -> AnalyticsScope:
    roles = user_roles(user)
    return AnalyticsScope(
        kind=(
            VisibilityKind.GLOBAL
            if FULL_ACCESS_ROLES.intersection(roles)
            else VisibilityKind.OWNED
        ),
        user_id=user.id,
        roles=tuple(sorted(roles)),
    )


def can_manage_outreach(user: User) -> bool:
    roles = user_roles(user)
    return bool(
        FULL_ACCESS_ROLES.intersection(roles)
        or roles.intersection({"manager", "analyst"})
    )


def can_approve_outreach(user: User) -> bool:
    roles = user_roles(user)
    return bool(FULL_ACCESS_ROLES.intersection(roles) or "manager" in roles)


def can_create_project(user: User) -> bool:
    roles = user_roles(user)
    return bool(FULL_ACCESS_ROLES.intersection(roles) or "manager" in roles)


def can_manage_project(user: User, project: Project) -> bool:
    return has_full_access(user) or (
        "manager" in user_roles(user) and project.owner_id == user.id
    )


def can_create_task(user: User, project: Project) -> bool:
    return can_manage_project(user, project)


def can_edit_task(user: User, task: Task, project: Project) -> bool:
    if can_manage_project(user, project):
        return True
    return "analyst" in user_roles(user) and task.assignee_id == user.id


def can_delete_task(user: User, project: Project) -> bool:
    return can_manage_project(user, project)


def can_manage_agents(user: User) -> bool:
    return has_full_access(user)


def can_invoke_agents(user: User) -> bool:
    return bool(
        user_roles(user).intersection(
            {"administrator", "executive", "manager", "analyst"}
        )
    )


def can_approve_agent_actions(user: User) -> bool:
    return bool(
        user_roles(user).intersection(
            {"administrator", "executive", "manager"}
        )
    )


def can_view_conversation(
    user: User,
    conversation: AgentConversation,
) -> bool:
    return has_full_access(user) or conversation.owner_id == user.id


def can_manage_memory(user: User, memory: AgentMemory | None = None) -> bool:
    if has_full_access(user):
        return True
    if memory is None:
        return can_invoke_agents(user)
    return memory.owner_id == user.id
