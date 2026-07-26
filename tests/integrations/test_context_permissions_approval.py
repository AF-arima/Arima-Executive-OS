import asyncio
from datetime import datetime, timezone

import pytest

from app.integrations.context import IntegrationExecutionContext
from app.integrations.exceptions import (
    IntegrationApprovalRequiredError,
    IntegrationPermissionDeniedError,
    IntegrationValidationError,
)
from app.integrations.factory import ConnectorFactory
from app.integrations.permissions import IntegrationPermissionValidator
from app.integrations.schemas import (
    ApprovalGrant,
    ApprovalOutcome,
    ApprovalPolicy,
    ConnectorOperation,
    IntegrationPermission,
)
from tests.database.helpers import sqlite_session
from tests.integrations.helpers import make_context


def test_context_and_layered_permissions_reject_unauthorized_access() -> None:
    async def scenario() -> None:
        async with sqlite_session() as session:
            context = await make_context(
                session,
                role_name="viewer",
                permissions=frozenset({IntegrationPermission.READ}),
            )
            operation = ConnectorFactory().create(
                "google_mail"
            ).supported_operations()[0]
            with pytest.raises(IntegrationPermissionDeniedError):
                IntegrationPermissionValidator().require(
                    context, operation, None
                )
            with pytest.raises(IntegrationValidationError):
                IntegrationExecutionContext(
                    user=context.user,
                    agent=context.agent,
                    conversation=context.conversation,
                    run=context.run,
                    user_permissions=context.user_permissions,
                    agent_permissions=context.agent_permissions,
                    integration_permissions=context.integration_permissions,
                    timezone="Invalid/Zone",
                )

    asyncio.run(scenario())


def test_user_admin_and_future_multistage_approvals() -> None:
    async def scenario() -> None:
        async with sqlite_session() as session:
            context = await make_context(session)
            validator = IntegrationPermissionValidator()
            send = ConnectorFactory().create(
                "google_mail"
            ).supported_operations()[-1]
            with pytest.raises(IntegrationApprovalRequiredError):
                validator.require(context, send, None)
            approved = ApprovalGrant(
                policy=ApprovalPolicy.USER,
                outcome=ApprovalOutcome.APPROVED,
                approved_by=context.user.id,
                approved_at=datetime.now(timezone.utc),
            )
            assert validator.require(
                context, send, approved
            ).approval_outcome is ApprovalOutcome.APPROVED
            delete_event = ConnectorFactory().create(
                "google_calendar"
            ).supported_operations()[-1]
            admin_approval = approved.model_copy(
                update={"policy": ApprovalPolicy.ADMIN}
            )
            assert validator.require(
                context, delete_event, admin_approval
            ).approval_outcome is ApprovalOutcome.APPROVED

            multistage = ConnectorOperation(
                name="future_sensitive_action",
                description="Future multi-stage action.",
                permissions=frozenset(
                    {
                        IntegrationPermission.WRITE,
                        IntegrationPermission.APPROVAL_REQUIRED,
                    }
                ),
                approval_policy=ApprovalPolicy.MULTI_STAGE,
            )
            pending = approved.model_copy(
                update={
                    "policy": ApprovalPolicy.MULTI_STAGE,
                    "completed_stages": 1,
                }
            )
            with pytest.raises(IntegrationApprovalRequiredError):
                validator.require(context, multistage, pending)
            complete = pending.model_copy(
                update={"completed_stages": 2}
            )
            assert validator.require(context, multistage, complete).allowed

    asyncio.run(scenario())
