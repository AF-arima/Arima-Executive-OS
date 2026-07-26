from __future__ import annotations

from dataclasses import dataclass

from app.database.models import AgentStatus
from app.services.permissions import can_view_conversation, user_roles
from app.tools.context import ToolExecutionContext
from app.tools.exceptions import ToolPermissionDeniedError
from app.tools.schemas import ToolPermission


ROLE_PERMISSIONS: dict[str, frozenset[ToolPermission]] = {
    "administrator": frozenset(ToolPermission),
    "executive": frozenset(ToolPermission),
    "manager": frozenset(
        {ToolPermission.READ, ToolPermission.WRITE, ToolPermission.AUDIT}
    ),
    "analyst": frozenset({ToolPermission.READ, ToolPermission.WRITE}),
    "viewer": frozenset({ToolPermission.READ}),
}


@dataclass(frozen=True, slots=True)
class PermissionDecision:
    allowed: bool
    missing_permissions: frozenset[ToolPermission]
    reason: str | None = None


class ToolPermissionValidator:
    def evaluate(
        self,
        context: ToolExecutionContext,
        required: frozenset[ToolPermission],
    ) -> PermissionDecision:
        if not context.current_user.is_active:
            return PermissionDecision(False, required, "User is inactive")
        if context.current_agent.status is not AgentStatus.ACTIVE:
            return PermissionDecision(False, required, "Agent is not active")
        if not can_view_conversation(
            context.current_user, context.conversation
        ):
            return PermissionDecision(
                False, required, "Conversation access denied"
            )
        role_permissions: set[ToolPermission] = set()
        for role in user_roles(context.current_user):
            role_permissions.update(ROLE_PERMISSIONS.get(role, ()))
        effective = frozenset(role_permissions).intersection(
            context.permissions
        )
        missing = required.difference(effective)
        return PermissionDecision(
            not missing,
            frozenset(missing),
            (
                None
                if not missing
                else "Missing permissions: "
                + ", ".join(sorted(item.value for item in missing))
            ),
        )

    def require(
        self,
        context: ToolExecutionContext,
        required: frozenset[ToolPermission],
    ) -> PermissionDecision:
        decision = self.evaluate(context, required)
        if not decision.allowed:
            raise ToolPermissionDeniedError(
                decision.reason or "Tool execution denied"
            )
        return decision
