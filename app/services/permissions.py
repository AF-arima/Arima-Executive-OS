from app.database.models import Project, Task, User

FULL_ACCESS_ROLES = frozenset({"administrator", "executive"})


def user_roles(user: User) -> frozenset[str]:
    return frozenset(role.name for role in user.roles)


def has_full_access(user: User) -> bool:
    return not FULL_ACCESS_ROLES.isdisjoint(user_roles(user))


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
    return (
        "analyst" in user_roles(user)
        and task.assignee_id == user.id
    )


def can_delete_task(user: User, project: Project) -> bool:
    return can_manage_project(user, project)
