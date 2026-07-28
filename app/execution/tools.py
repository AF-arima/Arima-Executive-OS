from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    AgentApprovalStatus,
    AgentRiskLevel,
    AuditAction,
    AuditEntity,
    ToolExecutionStatus,
    User,
)
from app.database.repositories import (
    AgentApprovalRepository,
    AgentConversationRepository,
    AgentRunRepository,
    AgentToolDefinitionRepository,
    AgentToolExecutionRepository,
)
from app.execution.exceptions import (
    ApprovalRequired,
    ExecutionCancelled,
    ToolFailure,
)
from app.execution.tool_adapters import ToolAdapterRegistry
from app.execution.types import (
    ExecutionContext,
    ToolInvocation,
    ToolResult,
)
from app.services.audit import record_audit
from app.services.exceptions import PermissionDeniedError
from app.services.notification import enqueue_agent_notification
from app.services.permissions import (
    can_invoke_agents,
    can_manage_memory,
    can_view_conversation,
)

UTC = timezone.utc


class ToolExecutionEngine:
    def __init__(
        self,
        session: AsyncSession,
        adapters: ToolAdapterRegistry,
    ) -> None:
        self.session = session
        self.adapters = adapters
        self.tools = AgentToolDefinitionRepository(session)
        self.executions = AgentToolExecutionRepository(session)
        self.approvals = AgentApprovalRepository(session)
        self.runs = AgentRunRepository(session)
        self.conversations = AgentConversationRepository(session)

    async def execute(
        self,
        *,
        run_id: UUID,
        invocation: ToolInvocation,
        context: ExecutionContext,
        actor: User,
    ) -> ToolResult:
        if not can_invoke_agents(actor):
            raise PermissionDeniedError
        if invocation.slug == "memory.write" and not can_manage_memory(actor):
            raise PermissionDeniedError
        run = await self.runs.get(run_id)
        if run is None:
            raise ToolFailure("Run not found")
        conversation = await self.conversations.get(run.conversation_id)
        if conversation is None:
            raise ToolFailure("Conversation not found")
        if not can_view_conversation(actor, conversation):
            raise PermissionDeniedError
        tool = await self.tools.get_by_slug(invocation.slug)
        if tool is None or not tool.is_enabled:
            raise ToolFailure(f"Tool is not enabled: {invocation.slug}")
        adapter = self.adapters.get(invocation.slug)
        adapter.validate(invocation.payload, context)

        execution = None
        if invocation.execution_id is not None:
            execution = await self.executions.get_for_update(
                invocation.execution_id
            )
            if (
                execution is None
                or execution.run_id != run.id
                or execution.tool_id != tool.id
            ):
                raise ToolFailure("Tool execution does not match invocation")
        if execution is None:
            execution = await self.executions.create(
                {
                    "run_id": run.id,
                    "tool_id": tool.id,
                    "status": ToolExecutionStatus.PENDING,
                    "input_payload": invocation.payload,
                }
            )

        if tool.requires_approval:
            await self._require_approval(
                execution_id=execution.id,
                run_id=run.id,
                tool_slug=tool.slug,
                risk_level=tool.risk_level,
                invocation=invocation,
                actor=actor,
                owner_id=conversation.owner_id,
            )

        started_at = datetime.now(UTC)
        execution = await self.executions.update(
            execution,
            {
                "status": ToolExecutionStatus.RUNNING,
                "started_at": started_at,
                "error_code": None,
                "error_message": None,
            },
        )
        try:
            output = await adapter.execute(invocation.payload, context)
        except Exception as error:
            await self.executions.update(
                execution,
                {
                    "status": ToolExecutionStatus.FAILED,
                    "error_code": type(error).__name__,
                    "error_message": str(error),
                    "completed_at": datetime.now(UTC),
                    "duration_ms": 0,
                },
            )
            self._audit(
                actor=actor,
                action=AuditAction.STATUS_CHANGE,
                entity_id=execution.id,
            )
            enqueue_agent_notification(
                self.session,
                user_id=conversation.owner_id,
                entity_type="agent_tool_execution",
                entity_id=execution.id,
                title="Agent tool failed",
                message=f"{tool.slug} failed in deterministic execution.",
            )
            await self.session.commit()
            if isinstance(error, ToolFailure):
                raise
            raise ToolFailure(str(error)) from error

        completed_at = datetime.now(UTC)
        execution = await self.executions.update(
            execution,
            {
                "status": ToolExecutionStatus.SUCCEEDED,
                "output_payload": output,
                "completed_at": completed_at,
                "duration_ms": max(
                    0,
                    int((completed_at - started_at).total_seconds() * 1000),
                ),
            },
        )
        self._audit(
            actor=actor,
            action=AuditAction.COMPLETE,
            entity_id=execution.id,
        )
        enqueue_agent_notification(
            self.session,
            user_id=conversation.owner_id,
            entity_type="agent_tool_execution",
            entity_id=execution.id,
            title="Agent tool executed",
            message=f"{tool.slug} completed in deterministic mock mode.",
        )
        await self.session.commit()
        return ToolResult(
            execution_id=execution.id,
            slug=tool.slug,
            output=output,
            duration_ms=execution.duration_ms or 0,
        )

    async def cancel(
        self,
        execution_id: UUID,
        actor: User,
    ) -> None:
        if not can_invoke_agents(actor):
            raise PermissionDeniedError
        execution = await self.executions.get_for_update(execution_id)
        if execution is None:
            raise ToolFailure("Tool execution not found")
        run = await self.runs.get(execution.run_id)
        if run is None:
            raise ToolFailure("Run not found")
        conversation = await self.conversations.get(run.conversation_id)
        if conversation is None:
            raise ToolFailure("Conversation not found")
        if not can_view_conversation(actor, conversation):
            raise PermissionDeniedError
        if execution.status in {
            ToolExecutionStatus.SUCCEEDED,
            ToolExecutionStatus.FAILED,
            ToolExecutionStatus.CANCELLED,
        }:
            raise ExecutionCancelled("Tool execution is already terminal")
        tool = await self.tools.get(execution.tool_id)
        if tool is None:
            raise ToolFailure("Tool not found")
        await self.adapters.get(tool.slug).cancel(execution.id)
        await self.executions.update(
            execution,
            {
                "status": ToolExecutionStatus.CANCELLED,
                "completed_at": datetime.now(UTC),
                "duration_ms": 0,
            },
        )
        self._audit(
            actor=actor,
            action=AuditAction.STATUS_CHANGE,
            entity_id=execution.id,
        )
        await self.session.commit()

    async def _require_approval(
        self,
        *,
        execution_id: UUID,
        run_id: UUID,
        tool_slug: str,
        risk_level: AgentRiskLevel,
        invocation: ToolInvocation,
        actor: User,
        owner_id: UUID,
    ) -> None:
        if invocation.approval_id is not None:
            approval = await self.approvals.get(invocation.approval_id)
            if (
                approval is None
                or approval.run_id != run_id
                or approval.tool_execution_id != execution_id
            ):
                raise ToolFailure("Approval does not match tool execution")
            if approval.status is AgentApprovalStatus.APPROVED:
                return
            if approval.status is AgentApprovalStatus.PENDING:
                raise ApprovalRequired(
                    tool_slug,
                    execution_id=execution_id,
                    approval_id=approval.id,
                )
            raise ExecutionCancelled("Tool approval was not granted")

        requested_at = datetime.now(UTC)
        approval = await self.approvals.create(
            {
                "run_id": run_id,
                "tool_execution_id": execution_id,
                "requested_by_id": actor.id,
                "action_type": tool_slug,
                "risk_level": risk_level,
                "status": AgentApprovalStatus.PENDING,
                "reason": f"Approval required for {tool_slug}",
                "request_payload": invocation.payload,
                "requested_at": requested_at,
            }
        )
        execution = await self.executions.get_for_update(execution_id)
        if execution is None:
            raise ToolFailure("Tool execution not found")
        await self.executions.update(
            execution,
            {"approval_id": approval.id},
        )
        self._audit(
            actor=actor,
            action=AuditAction.CREATE,
            entity_id=approval.id,
        )
        enqueue_agent_notification(
            self.session,
            user_id=owner_id,
            entity_type="agent_approval",
            entity_id=approval.id,
            title="Agent approval requested",
            message=f"Approval requested for {tool_slug}.",
        )
        await self.session.commit()
        raise ApprovalRequired(
            tool_slug,
            execution_id=execution_id,
            approval_id=approval.id,
        )

    def _audit(
        self,
        *,
        actor: User,
        action: AuditAction,
        entity_id: UUID,
    ) -> None:
        record_audit(
            self.session,
            actor_id=actor.id,
            action=action,
            entity=AuditEntity.AUTOMATION,
            entity_id=entity_id,
        )
