from typing import Literal

from pydantic import Field

from app.schemas.auth import StrictSchema

GateStatus = Literal["pass", "fail", "not_evaluated"]


class VoiceAuthorizationGates(StrictSchema):
    """Safe, non-identifier authorization results for a Voice session."""

    active_verified_user: GateStatus
    voice_session_owner: GateStatus
    conversation_owner: GateStatus
    workspace_affinity: GateStatus
    workspace_membership: GateStatus
    active_agent: GateStatus
    workspace_agent_grant: GateStatus
    can_invoke_agents: GateStatus
    run_triggered_by_actor: GateStatus = "not_evaluated"
    run_agent_match: GateStatus = "not_evaluated"
    run_workspace_affinity: GateStatus = "not_evaluated"
    run_binding: GateStatus = "not_evaluated"


class VoiceAuthorizationDiagnostic(StrictSchema):
    """Redacted diagnostic response for an explicitly authorized operator."""

    authorized: bool
    first_failing_gate: str | None = None
    first_failing_reason: str | None = None
    actor_active: bool
    actor_verified: bool
    actor_roles: list[str] = Field(default_factory=list)
    gates: VoiceAuthorizationGates
