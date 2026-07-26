from __future__ import annotations

from dataclasses import dataclass

from app.database.models import AgentStatus
from app.integrations.context import IntegrationExecutionContext
from app.integrations.exceptions import (
    IntegrationApprovalRequiredError,
    IntegrationPermissionDeniedError,
)
from app.integrations.schemas import (
    ApprovalGrant,
    ApprovalOutcome,
    ApprovalPolicy,
    ConnectorOperation,
    IntegrationPermission,
)
from app.services.permissions import can_view_conversation, user_roles


ROLE_PERMISSIONS: dict[str, frozenset[IntegrationPermission]] = {
    "administrator": frozenset(IntegrationPermission),
    "executive": frozenset(
        {
            IntegrationPermission.READ,
            IntegrationPermission.WRITE,
            IntegrationPermission.APPROVAL_REQUIRED,
            IntegrationPermission.SENSITIVE_DATA,
        }
    ),
    "manager": frozenset(
        {
            IntegrationPermission.READ,
            IntegrationPermission.WRITE,
            IntegrationPermission.APPROVAL_REQUIRED,
        }
    ),
    "analyst": frozenset(
        {IntegrationPermission.READ, IntegrationPermission.WRITE}
    ),
    "viewer": frozenset({IntegrationPermission.READ}),
}


@dataclass(frozen=True, slots=True)
class IntegrationPermissionDecision:
    allowed: bool
    missing_permissions: frozenset[IntegrationPermission]
    approval_outcome: ApprovalOutcome
    reason: str | None = None


class IntegrationPermissionValidator:
    def evaluate(
        self,
        context: IntegrationExecutionContext,
        operation: ConnectorOperation,
        approval: ApprovalGrant | None,
    ) -> IntegrationPermissionDecision:
        if not context.user.is_active:
            return self._denied(operation, "User is inactive")
        if context.agent.status is not AgentStatus.ACTIVE:
            return self._denied(operation, "Agent is not active")
        if not can_view_conversation(context.user, context.conversation):
            return self._denied(operation, "Conversation access denied")
        role_permissions: set[IntegrationPermission] = set()
        for role in user_roles(context.user):
            role_permissions.update(ROLE_PERMISSIONS.get(role, ()))
        effective = context.permissions & frozenset(role_permissions)
        missing = operation.permissions - effective
        if missing:
            return IntegrationPermissionDecision(
                False,
                frozenset(missing),
                ApprovalOutcome.DENIED,
                "Missing permissions: "
                + ", ".join(sorted(item.value for item in missing)),
            )
        approval_outcome = self._approval_outcome(
            operation.approval_policy, approval
        )
        return IntegrationPermissionDecision(
            approval_outcome
            in {ApprovalOutcome.NOT_REQUIRED, ApprovalOutcome.APPROVED},
            frozenset(),
            approval_outcome,
            (
                None
                if approval_outcome
                in {ApprovalOutcome.NOT_REQUIRED, ApprovalOutcome.APPROVED}
                else "Required approval has not been granted"
            ),
        )

    def require(
        self,
        context: IntegrationExecutionContext,
        operation: ConnectorOperation,
        approval: ApprovalGrant | None,
    ) -> IntegrationPermissionDecision:
        decision = self.evaluate(context, operation, approval)
        if decision.missing_permissions:
            raise IntegrationPermissionDeniedError(
                decision.reason or "Integration permission denied"
            )
        if not decision.allowed:
            raise IntegrationApprovalRequiredError(
                decision.reason or "Integration approval required"
            )
        return decision

    @staticmethod
    def _approval_outcome(
        policy: ApprovalPolicy,
        approval: ApprovalGrant | None,
    ) -> ApprovalOutcome:
        if policy is ApprovalPolicy.NONE:
            return ApprovalOutcome.NOT_REQUIRED
        if approval is None:
            return ApprovalOutcome.PENDING
        if approval.outcome is not ApprovalOutcome.APPROVED:
            return approval.outcome
        if approval.policy is not policy:
            return ApprovalOutcome.DENIED
        if approval.approved_by is None or approval.approved_at is None:
            return ApprovalOutcome.DENIED
        if (
            policy is ApprovalPolicy.MULTI_STAGE
            and approval.completed_stages < 2
        ):
            return ApprovalOutcome.PENDING
        return ApprovalOutcome.APPROVED

    @staticmethod
    def _denied(
        operation: ConnectorOperation, reason: str
    ) -> IntegrationPermissionDecision:
        return IntegrationPermissionDecision(
            False,
            operation.permissions,
            ApprovalOutcome.DENIED,
            reason,
        )
