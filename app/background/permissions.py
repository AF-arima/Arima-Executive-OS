from __future__ import annotations

from dataclasses import dataclass

from app.background.context import BackgroundExecutionContext
from app.background.exceptions import (
    BackgroundApprovalRequiredError,
    BackgroundPermissionDeniedError,
)
from app.background.schemas import (
    ApprovalGrant,
    ApprovalOutcome,
    ApprovalPolicy,
    BackgroundPermission,
)
from app.database.models import AgentStatus
from app.services.permissions import can_view_conversation, user_roles


ROLE_PERMISSIONS: dict[str, frozenset[BackgroundPermission]] = {
    "administrator": frozenset(BackgroundPermission),
    "executive": frozenset(
        item
        for item in BackgroundPermission
        if item is not BackgroundPermission.ADMIN
    ),
    "manager": frozenset(
        {
            BackgroundPermission.READ,
            BackgroundPermission.WRITE,
            BackgroundPermission.APPROVAL_REQUIRED,
            BackgroundPermission.EXECUTE_TOOL,
            BackgroundPermission.EXECUTE_INTEGRATION,
            BackgroundPermission.EXECUTE_AGENT,
        }
    ),
    "analyst": frozenset(
        {
            BackgroundPermission.READ,
            BackgroundPermission.EXECUTE_TOOL,
        }
    ),
    "viewer": frozenset({BackgroundPermission.READ}),
}


@dataclass(frozen=True, slots=True)
class BackgroundPermissionDecision:
    allowed: bool
    missing_permissions: frozenset[BackgroundPermission]
    approval_outcome: ApprovalOutcome
    reason: str | None = None


class BackgroundPermissionValidator:
    def evaluate(
        self,
        context: BackgroundExecutionContext,
        approval: ApprovalGrant | None,
    ) -> BackgroundPermissionDecision:
        required = context.job.required_permissions()
        if not context.user.is_active:
            return self._denied(required, "User is inactive")
        if context.agent.status is not AgentStatus.ACTIVE:
            return self._denied(required, "Agent is inactive")
        if not can_view_conversation(context.user, context.conversation):
            return self._denied(required, "Conversation access denied")
        role_permissions: set[BackgroundPermission] = set()
        for role in user_roles(context.user):
            role_permissions.update(ROLE_PERMISSIONS.get(role, ()))
        effective = context.permissions & frozenset(role_permissions)
        missing = required - effective
        if missing:
            return BackgroundPermissionDecision(
                False,
                frozenset(missing),
                ApprovalOutcome.DENIED,
                "Missing permissions: "
                + ", ".join(sorted(item.value for item in missing)),
            )
        outcome = self._approval(
            context.job.required_approval_policy(), approval
        )
        allowed = outcome in {
            ApprovalOutcome.NOT_REQUIRED,
            ApprovalOutcome.APPROVED,
        }
        return BackgroundPermissionDecision(
            allowed,
            frozenset(),
            outcome,
            None if allowed else "Background approval required",
        )

    def require(
        self,
        context: BackgroundExecutionContext,
        approval: ApprovalGrant | None,
    ) -> BackgroundPermissionDecision:
        decision = self.evaluate(context, approval)
        if decision.missing_permissions:
            raise BackgroundPermissionDeniedError(
                decision.reason or "Background permission denied"
            )
        if not decision.allowed:
            raise BackgroundApprovalRequiredError(
                decision.reason or "Background approval required"
            )
        return decision

    @staticmethod
    def _approval(
        policy: ApprovalPolicy,
        grant: ApprovalGrant | None,
    ) -> ApprovalOutcome:
        if policy is ApprovalPolicy.NONE:
            return ApprovalOutcome.NOT_REQUIRED
        if grant is None:
            return ApprovalOutcome.PENDING
        if (
            grant.policy is not policy
            or grant.outcome is not ApprovalOutcome.APPROVED
            or grant.approved_by is None
            or grant.approved_at is None
        ):
            return ApprovalOutcome.DENIED
        if (
            policy is ApprovalPolicy.MULTI_STAGE
            and grant.completed_stages < 2
        ):
            return ApprovalOutcome.PENDING
        return ApprovalOutcome.APPROVED

    @staticmethod
    def _denied(
        required: frozenset[BackgroundPermission], reason: str
    ) -> BackgroundPermissionDecision:
        return BackgroundPermissionDecision(
            False, required, ApprovalOutcome.DENIED, reason
        )
