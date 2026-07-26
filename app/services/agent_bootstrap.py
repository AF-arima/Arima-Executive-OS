from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.agent import (
    AgentDefinition,
    AgentRiskLevel,
    AgentStatus,
    AgentToolDefinition,
    ToolExecutionMode,
)
from app.database.repositories.agent import (
    AgentDefinitionRepository,
    AgentToolDefinitionRepository,
)

DEFAULT_AGENT_SLUG = "executive-assistant"
DEFAULT_AGENT_INSTRUCTIONS = (
    "Support authorised Arima Executive OS users with concise, accurate "
    "operational assistance. Respect role permissions, request approval "
    "before sensitive actions, and never claim an action was completed "
    "unless the system confirms it."
)

FOUNDATION_TOOLS: tuple[dict[str, object], ...] = (
    {
        "slug": "projects.read",
        "name": "Read projects",
        "description": "Read authorised project records.",
        "category": "projects",
        "risk_level": AgentRiskLevel.LOW,
        "execution_mode": ToolExecutionMode.INTERNAL,
        "requires_approval": False,
    },
    {
        "slug": "tasks.read",
        "name": "Read tasks",
        "description": "Read authorised task records.",
        "category": "tasks",
        "risk_level": AgentRiskLevel.LOW,
        "execution_mode": ToolExecutionMode.INTERNAL,
        "requires_approval": False,
    },
    {
        "slug": "analytics.read",
        "name": "Read analytics",
        "description": "Read permission-scoped operational analytics.",
        "category": "analytics",
        "risk_level": AgentRiskLevel.LOW,
        "execution_mode": ToolExecutionMode.INTERNAL,
        "requires_approval": False,
    },
    {
        "slug": "crm.read",
        "name": "Read CRM",
        "description": "Read authorised CRM records.",
        "category": "crm",
        "risk_level": AgentRiskLevel.MEDIUM,
        "execution_mode": ToolExecutionMode.INTERNAL,
        "requires_approval": False,
    },
    {
        "slug": "outreach.read",
        "name": "Read outreach",
        "description": "Read authorised outreach records.",
        "category": "outreach",
        "risk_level": AgentRiskLevel.MEDIUM,
        "execution_mode": ToolExecutionMode.INTERNAL,
        "requires_approval": False,
    },
    {
        "slug": "notifications.read",
        "name": "Read notifications",
        "description": "Read the current user's notifications.",
        "category": "notifications",
        "risk_level": AgentRiskLevel.LOW,
        "execution_mode": ToolExecutionMode.INTERNAL,
        "requires_approval": False,
    },
    {
        "slug": "memory.read",
        "name": "Read memory",
        "description": "Read active, authorised agent memory.",
        "category": "memory",
        "risk_level": AgentRiskLevel.MEDIUM,
        "execution_mode": ToolExecutionMode.INTERNAL,
        "requires_approval": False,
    },
    {
        "slug": "memory.write",
        "name": "Write memory",
        "description": "Create or update authorised agent memory.",
        "category": "memory",
        "risk_level": AgentRiskLevel.HIGH,
        "execution_mode": ToolExecutionMode.DEFERRED,
        "requires_approval": True,
    },
    {
        "slug": "approvals.request",
        "name": "Request approval",
        "description": "Create an approval request for a proposed action.",
        "category": "approvals",
        "risk_level": AgentRiskLevel.MEDIUM,
        "execution_mode": ToolExecutionMode.DEFERRED,
        "requires_approval": False,
    },
)


@dataclass(frozen=True, slots=True)
class AgentBootstrapResult:
    agent: AgentDefinition
    tools: tuple[AgentToolDefinition, ...]


async def bootstrap_agent_platform(
    session: AsyncSession,
    *,
    created_by_id: UUID,
) -> AgentBootstrapResult:
    agents = AgentDefinitionRepository(session)
    tools = AgentToolDefinitionRepository(session)

    agent = await agents.get_by_slug(
        DEFAULT_AGENT_SLUG,
        include_archived=True,
    )
    managed_agent_fields = {
        "name": "Executive Assistant",
        "description": "Default operational assistant for Arima Executive OS.",
        "system_instructions": DEFAULT_AGENT_INSTRUCTIONS,
        "status": AgentStatus.ACTIVE,
        "version": 1,
        "archived_at": None,
    }
    if agent is None:
        agent = await agents.create(
            {
                "slug": DEFAULT_AGENT_SLUG,
                **managed_agent_fields,
                "is_default": False,
                "created_by_id": created_by_id,
            }
        )
    else:
        await agents.update(agent, managed_agent_fields)
    default_agent = await agents.set_active_default(agent.id)
    if default_agent is None:
        raise RuntimeError("Default agent could not be established")

    bootstrapped_tools: list[AgentToolDefinition] = []
    for definition in FOUNDATION_TOOLS:
        slug = str(definition["slug"])
        tool = await tools.get_by_slug(slug)
        managed_tool_fields = {
            key: value
            for key, value in definition.items()
            if key != "slug"
        }
        managed_tool_fields.update(
            {
                "input_schema": {"type": "object"},
                "output_schema": {"type": "object"},
            }
        )
        if tool is None:
            tool = await tools.create(
                {
                    "slug": slug,
                    **managed_tool_fields,
                    "is_enabled": True,
                }
            )
        else:
            await tools.update(tool, managed_tool_fields)
        bootstrapped_tools.append(tool)

    await session.commit()
    return AgentBootstrapResult(
        agent=default_agent,
        tools=tuple(bootstrapped_tools),
    )
