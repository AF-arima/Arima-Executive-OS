from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response, status

from app.api.v1.dependencies import (
    AUTHENTICATED_RESPONSES,
    AnalyticsUser,
    SessionDependency,
)
from app.database.models import (
    AgentApproval,
    AgentApprovalStatus,
    AgentConversation,
    AgentDefinition,
    AgentMemory,
    AgentMemoryScope,
    AgentMessage,
    AgentRun,
    AgentRunStatus,
    AgentStatus,
    ConversationStatus,
    MessageRole,
)
from app.schemas.agent import (
    AgentApprovalFilter,
    AgentApprovalList,
    AgentApprovalRead,
    AgentCreateRequest,
    AgentDefinitionFilter,
    AgentDefinitionList,
    AgentDefinitionRead,
    AgentMemoryFilter,
    AgentMemoryList,
    AgentMemoryRead,
    AgentMessageList,
    AgentMessageRead,
    AgentPatchRequest,
    AgentRunFilter,
    AgentRunList,
    AgentRunRead,
    ApprovalCreateRequest,
    ApprovalDecisionRequest,
    ConversationCreateRequest,
    ConversationRenameRequest,
    AgentConversationList,
    AgentConversationRead,
    MemoryCreateRequest,
    MemoryPatchRequest,
    MessageCreateRequest,
    RunCreateRequest,
    RunTransitionRequest,
)
from app.services.agent import (
    AgentService,
    ApprovalService,
    ConversationService,
    MemoryService,
    MessageService,
    RunService,
)
from app.services.exceptions import ResourceConflictError

router = APIRouter(tags=["agent-platform"])
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0)]


@router.post(
    "/agents",
    response_model=AgentDefinitionRead,
    status_code=status.HTTP_201_CREATED,
    responses=AUTHENTICATED_RESPONSES,
)
async def create_agent(
    data: AgentCreateRequest,
    session: SessionDependency,
    actor: AnalyticsUser,
) -> AgentDefinition:
    return await AgentService(session).create(data, actor)


@router.get("/agents", response_model=AgentDefinitionList)
async def list_agents(
    session: SessionDependency,
    actor: AnalyticsUser,
    agent_status: Annotated[
        AgentStatus | None,
        Query(alias="status"),
    ] = None,
    is_default: bool | None = None,
    include_archived: bool = False,
    limit: Limit = 50,
    offset: Offset = 0,
) -> AgentDefinitionList:
    del actor
    page = await AgentService(session).list(
        AgentDefinitionFilter(
            status=agent_status,
            is_default=is_default,
            include_archived=include_archived,
            limit=limit,
            offset=offset,
        )
    )
    return AgentDefinitionList(
        items=[
            AgentDefinitionRead.model_validate(item)
            for item in page.items
        ],
        total=page.total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/agents/default", response_model=AgentDefinitionRead)
async def get_default_agent(
    session: SessionDependency,
    actor: AnalyticsUser,
) -> AgentDefinition:
    del actor
    return await AgentService(session).get_default()


@router.get("/agents/{agent_id}", response_model=AgentDefinitionRead)
async def get_agent(
    agent_id: UUID,
    session: SessionDependency,
    actor: AnalyticsUser,
) -> AgentDefinition:
    del actor
    return await AgentService(session).get(agent_id)


@router.patch("/agents/{agent_id}", response_model=AgentDefinitionRead)
async def update_agent(
    agent_id: UUID,
    data: AgentPatchRequest,
    session: SessionDependency,
    actor: AnalyticsUser,
) -> AgentDefinition:
    return await AgentService(session).update(agent_id, data, actor)


@router.patch(
    "/agents/{agent_id}/activate",
    response_model=AgentDefinitionRead,
)
async def activate_agent(
    agent_id: UUID,
    session: SessionDependency,
    actor: AnalyticsUser,
) -> AgentDefinition:
    return await AgentService(session).activate(agent_id, actor)


@router.patch(
    "/agents/{agent_id}/disable",
    response_model=AgentDefinitionRead,
)
async def disable_agent(
    agent_id: UUID,
    session: SessionDependency,
    actor: AnalyticsUser,
) -> AgentDefinition:
    return await AgentService(session).disable(agent_id, actor)


@router.patch(
    "/agents/{agent_id}/archive",
    response_model=AgentDefinitionRead,
)
async def archive_agent(
    agent_id: UUID,
    session: SessionDependency,
    actor: AnalyticsUser,
) -> AgentDefinition:
    return await AgentService(session).archive(agent_id, actor)


@router.patch(
    "/agents/{agent_id}/default",
    response_model=AgentDefinitionRead,
)
async def set_default_agent(
    agent_id: UUID,
    session: SessionDependency,
    actor: AnalyticsUser,
) -> AgentDefinition:
    return await AgentService(session).set_default(agent_id, actor)


@router.post(
    "/conversations",
    response_model=AgentConversationRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_conversation(
    data: ConversationCreateRequest,
    session: SessionDependency,
    actor: AnalyticsUser,
) -> AgentConversation:
    return await ConversationService(session).create(data, actor)


@router.get("/conversations", response_model=AgentConversationList)
async def list_conversations(
    session: SessionDependency,
    actor: AnalyticsUser,
    owner_id: UUID | None = None,
    agent_id: UUID | None = None,
    conversation_status: Annotated[
        ConversationStatus | None,
        Query(alias="status"),
    ] = None,
    pinned: bool | None = None,
    include_archived: bool = False,
    limit: Limit = 50,
    offset: Offset = 0,
) -> AgentConversationList:
    page = await ConversationService(session).list_user_conversations(
        actor,
        owner_id=owner_id,
        agent_id=agent_id,
        status=conversation_status,
        pinned=pinned,
        include_archived=include_archived,
        limit=limit,
        offset=offset,
    )
    return AgentConversationList(
        items=[
            AgentConversationRead.model_validate(item)
            for item in page.items
        ],
        total=page.total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get(
    "/conversations/{conversation_id}",
    response_model=AgentConversationRead,
)
async def get_conversation(
    conversation_id: UUID,
    session: SessionDependency,
    actor: AnalyticsUser,
) -> AgentConversation:
    return await ConversationService(session).get(conversation_id, actor)


@router.patch(
    "/conversations/{conversation_id}",
    response_model=AgentConversationRead,
)
async def rename_conversation(
    conversation_id: UUID,
    data: ConversationRenameRequest,
    session: SessionDependency,
    actor: AnalyticsUser,
) -> AgentConversation:
    return await ConversationService(session).rename(
        conversation_id,
        data,
        actor,
    )


@router.patch(
    "/conversations/{conversation_id}/archive",
    response_model=AgentConversationRead,
)
async def archive_conversation(
    conversation_id: UUID,
    session: SessionDependency,
    actor: AnalyticsUser,
) -> AgentConversation:
    return await ConversationService(session).archive(
        conversation_id,
        actor,
    )


@router.patch(
    "/conversations/{conversation_id}/close",
    response_model=AgentConversationRead,
)
async def close_conversation(
    conversation_id: UUID,
    session: SessionDependency,
    actor: AnalyticsUser,
) -> AgentConversation:
    return await ConversationService(session).close(
        conversation_id,
        actor,
    )


@router.patch(
    "/conversations/{conversation_id}/pin",
    response_model=AgentConversationRead,
)
async def pin_conversation(
    conversation_id: UUID,
    session: SessionDependency,
    actor: AnalyticsUser,
) -> AgentConversation:
    return await ConversationService(session).pin(
        conversation_id,
        actor,
    )


@router.patch(
    "/conversations/{conversation_id}/unpin",
    response_model=AgentConversationRead,
)
async def unpin_conversation(
    conversation_id: UUID,
    session: SessionDependency,
    actor: AnalyticsUser,
) -> AgentConversation:
    return await ConversationService(session).unpin(
        conversation_id,
        actor,
    )


@router.post(
    "/messages",
    response_model=AgentMessageRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_message(
    data: MessageCreateRequest,
    session: SessionDependency,
    actor: AnalyticsUser,
) -> AgentMessage:
    service = MessageService(session)
    if data.role is MessageRole.USER:
        return await service.create_user_message(data, actor)
    if data.role is MessageRole.ASSISTANT:
        return await service.create_assistant_message(data, actor)
    if data.role is MessageRole.TOOL:
        return await service.create_tool_message(data, actor)
    if data.role is MessageRole.APPROVAL:
        return await service.create_approval_message(data, actor)
    raise ResourceConflictError("System messages are not user-creatable")


@router.get("/messages", response_model=AgentMessageList)
async def list_messages(
    conversation_id: UUID,
    session: SessionDependency,
    actor: AnalyticsUser,
    limit: Limit = 100,
    offset: Offset = 0,
) -> AgentMessageList:
    page = await MessageService(session).get_conversation_messages(
        conversation_id,
        actor,
        limit=limit,
        offset=offset,
    )
    return AgentMessageList(
        items=[AgentMessageRead.model_validate(item) for item in page.items],
        total=page.total,
        limit=page.limit,
        offset=page.offset,
    )


@router.post(
    "/runs",
    response_model=AgentRunRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_run(
    data: RunCreateRequest,
    session: SessionDependency,
    actor: AnalyticsUser,
) -> AgentRun:
    return await RunService(session).create(data, actor)


@router.get("/runs", response_model=AgentRunList)
async def list_runs(
    session: SessionDependency,
    actor: AnalyticsUser,
    conversation_id: UUID | None = None,
    agent_id: UUID | None = None,
    triggered_by_id: UUID | None = None,
    run_status: Annotated[
        AgentRunStatus | None,
        Query(alias="status"),
    ] = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> AgentRunList:
    page = await RunService(session).list(
        AgentRunFilter(
            conversation_id=conversation_id,
            agent_id=agent_id,
            triggered_by_id=triggered_by_id,
            status=run_status,
            limit=limit,
            offset=offset,
        ),
        actor,
    )
    return AgentRunList(
        items=[AgentRunRead.model_validate(item) for item in page.items],
        total=page.total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/runs/{run_id}", response_model=AgentRunRead)
async def get_run(
    run_id: UUID,
    session: SessionDependency,
    actor: AnalyticsUser,
) -> AgentRun:
    return await RunService(session).get(run_id, actor)


@router.patch("/runs/{run_id}", response_model=AgentRunRead)
async def transition_run(
    run_id: UUID,
    data: RunTransitionRequest,
    session: SessionDependency,
    actor: AnalyticsUser,
) -> AgentRun:
    return await RunService(session).transition(run_id, data, actor)


@router.post(
    "/approvals",
    response_model=AgentApprovalRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_approval(
    data: ApprovalCreateRequest,
    session: SessionDependency,
    actor: AnalyticsUser,
) -> AgentApproval:
    return await ApprovalService(session).create(data, actor)


@router.get("/approvals", response_model=AgentApprovalList)
async def list_approvals(
    session: SessionDependency,
    actor: AnalyticsUser,
    run_id: UUID | None = None,
    approval_status: Annotated[
        AgentApprovalStatus | None,
        Query(alias="status"),
    ] = None,
    requested_by_id: UUID | None = None,
    decided_by_id: UUID | None = None,
    unexpired_only: bool = False,
    limit: Limit = 50,
    offset: Offset = 0,
) -> AgentApprovalList:
    page = await ApprovalService(session).list(
        AgentApprovalFilter(
            run_id=run_id,
            status=approval_status,
            requested_by_id=requested_by_id,
            decided_by_id=decided_by_id,
            unexpired_only=unexpired_only,
            limit=limit,
            offset=offset,
        ),
        actor,
    )
    return AgentApprovalList(
        items=[
            AgentApprovalRead.model_validate(item)
            for item in page.items
        ],
        total=page.total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/approvals/pending", response_model=AgentApprovalList)
async def list_pending_approvals(
    session: SessionDependency,
    actor: AnalyticsUser,
    limit: Limit = 50,
    offset: Offset = 0,
) -> AgentApprovalList:
    page = await ApprovalService(session).list_pending(
        actor,
        limit=limit,
        offset=offset,
    )
    return AgentApprovalList(
        items=[
            AgentApprovalRead.model_validate(item)
            for item in page.items
        ],
        total=page.total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/approvals/{approval_id}", response_model=AgentApprovalRead)
async def get_approval(
    approval_id: UUID,
    session: SessionDependency,
    actor: AnalyticsUser,
) -> AgentApproval:
    return await ApprovalService(session).get(approval_id, actor)


@router.patch(
    "/approvals/{approval_id}",
    response_model=AgentApprovalRead,
)
async def decide_approval(
    approval_id: UUID,
    data: ApprovalDecisionRequest,
    session: SessionDependency,
    actor: AnalyticsUser,
) -> AgentApproval:
    service = ApprovalService(session)
    if data.status is AgentApprovalStatus.APPROVED:
        return await service.approve(
            approval_id,
            actor,
            decision_note=data.decision_note,
        )
    if data.status is AgentApprovalStatus.REJECTED:
        return await service.reject(
            approval_id,
            actor,
            decision_note=data.decision_note,
        )
    if data.status is AgentApprovalStatus.CANCELLED:
        return await service.cancel(
            approval_id,
            actor,
            decision_note=data.decision_note,
        )
    if data.status is AgentApprovalStatus.EXPIRED:
        return await service.expire(approval_id, actor)
    raise ResourceConflictError("Approval must receive a terminal decision")


@router.post(
    "/memory",
    response_model=AgentMemoryRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_memory(
    data: MemoryCreateRequest,
    session: SessionDependency,
    actor: AnalyticsUser,
) -> AgentMemory:
    return await MemoryService(session).create(data, actor)


@router.get("/memory", response_model=AgentMemoryList)
async def list_memory(
    session: SessionDependency,
    actor: AnalyticsUser,
    owner_id: UUID | None = None,
    agent_id: UUID | None = None,
    conversation_id: UUID | None = None,
    scope: AgentMemoryScope | None = None,
    key: Annotated[str | None, Query(max_length=200)] = None,
    active_unexpired_only: bool = True,
    limit: Limit = 50,
    offset: Offset = 0,
) -> AgentMemoryList:
    page = await MemoryService(session).list_active(
        AgentMemoryFilter(
            owner_id=owner_id,
            agent_id=agent_id,
            conversation_id=conversation_id,
            scope=scope,
            key=key,
            active_unexpired_only=active_unexpired_only,
            limit=limit,
            offset=offset,
        ),
        actor,
    )
    return AgentMemoryList(
        items=[AgentMemoryRead.model_validate(item) for item in page.items],
        total=page.total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/memory/search", response_model=AgentMemoryList)
async def search_memory(
    scope: AgentMemoryScope,
    session: SessionDependency,
    actor: AnalyticsUser,
    owner_id: UUID | None = None,
    agent_id: UUID | None = None,
    conversation_id: UUID | None = None,
    key: Annotated[str | None, Query(max_length=200)] = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> AgentMemoryList:
    page = await MemoryService(session).search_by_scope(
        scope=scope,
        actor=actor,
        owner_id=owner_id,
        agent_id=agent_id,
        conversation_id=conversation_id,
        key=key,
        limit=limit,
        offset=offset,
    )
    return AgentMemoryList(
        items=[AgentMemoryRead.model_validate(item) for item in page.items],
        total=page.total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/memory/{memory_id}", response_model=AgentMemoryRead)
async def get_memory(
    memory_id: UUID,
    session: SessionDependency,
    actor: AnalyticsUser,
) -> AgentMemory:
    return await MemoryService(session).get(memory_id, actor)


@router.patch("/memory/{memory_id}", response_model=AgentMemoryRead)
async def update_memory(
    memory_id: UUID,
    data: MemoryPatchRequest,
    session: SessionDependency,
    actor: AnalyticsUser,
) -> AgentMemory:
    return await MemoryService(session).update(memory_id, data, actor)


@router.patch(
    "/memory/{memory_id}/disable",
    response_model=AgentMemoryRead,
)
async def disable_memory(
    memory_id: UUID,
    session: SessionDependency,
    actor: AnalyticsUser,
) -> AgentMemory:
    return await MemoryService(session).disable(memory_id, actor)


@router.delete(
    "/memory/{memory_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_memory(
    memory_id: UUID,
    session: SessionDependency,
    actor: AnalyticsUser,
) -> Response:
    await MemoryService(session).delete(memory_id, actor)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
