from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.models import (
    AIWorkspaceRun,
    AgentConversation,
    AgentDefinition,
    AgentRun,
    AuditAction,
    AuditEntity,
    User,
    VoiceSessionRecord,
)
from app.intelligence.access import (
    AgentGrantService,
    IntelligenceAccessError,
    require_workspace_affinity,
    require_workspace_membership,
)
from app.schemas.voice_diagnostic import (
    GateStatus,
    VoiceAuthorizationDiagnostic,
    VoiceAuthorizationGates,
)
from app.services.audit import record_audit
from app.services.exceptions import ResourceNotFoundError
from app.services.permissions import can_invoke_agents, user_roles


@dataclass(frozen=True, slots=True)
class _Gate:
    status: GateStatus
    reason: str | None = None


class VoiceAuthorizationDiagnosticService:
    """Read-only inspection of one Voice session's authorization path.

    The caller is authorized by the existing Founder Control dependency. The
    session owner is treated as the subject under investigation; only safe
    booleans, role names, and static failure categories are returned.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def inspect(
        self,
        session_id: UUID,
        *,
        operator: User,
    ) -> VoiceAuthorizationDiagnostic:
        voice_session = await self.session.get(VoiceSessionRecord, session_id)
        if voice_session is None:
            raise ResourceNotFoundError("Voice session not found")

        actor = await self.session.scalar(
            select(User)
            .options(selectinload(User.roles))
            .where(User.id == voice_session.user_id)
        )
        if actor is None:
            raise ResourceNotFoundError("Voice session owner not found")

        gates: dict[str, _Gate] = {}
        gates["active_verified_user"] = self._active_verified_gate(actor)
        gates["voice_session_owner"] = self._session_owner_gate(
            voice_session.user_id,
            actor.id,
        )

        conversation: AgentConversation | None = None
        if voice_session.conversation_id is None:
            gates["conversation_owner"] = _Gate(
                "fail", "conversation_missing"
            )
        else:
            conversation = await self.session.get(
                AgentConversation,
                voice_session.conversation_id,
            )
            if conversation is None:
                gates["conversation_owner"] = _Gate(
                    "fail", "conversation_missing"
                )
            elif conversation.owner_id != actor.id:
                gates["conversation_owner"] = _Gate(
                    "fail", "conversation_owner_mismatch"
                )
            else:
                gates["conversation_owner"] = _Gate("pass")

        workspace_id: UUID | None = None
        agent: AgentDefinition | None = None
        if conversation is not None:
            agent = await self.session.get(
                AgentDefinition,
                conversation.agent_id,
            )
            if agent is None or agent.status.value != "active":
                gates["active_agent"] = _Gate("fail", "agent_inactive_or_missing")
            elif agent.archived_at is not None:
                gates["active_agent"] = _Gate("fail", "agent_archived")
            else:
                gates["active_agent"] = _Gate("pass")

            raw_workspace_id = conversation.metadata_.get("workspace_id")
            try:
                workspace_id = UUID(str(raw_workspace_id))
            except (TypeError, ValueError):
                gates["workspace_affinity"] = _Gate(
                    "fail", "workspace_metadata_missing_or_invalid"
                )
                gates["workspace_membership"] = _Gate("not_evaluated")
                gates["workspace_agent_grant"] = _Gate("not_evaluated")
            else:
                run = None
                if voice_session.run_id is not None:
                    run = await self.session.get(AgentRun, voice_session.run_id)
                self._evaluate_run_gates(
                    gates,
                    conversation=conversation,
                    run=run,
                    actor=actor,
                    workspace_id=workspace_id,
                )
                if run is not None:
                    binding = await self.session.scalar(
                        select(AIWorkspaceRun).where(
                            AIWorkspaceRun.run_id == run.id
                        )
                    )
                    if binding is None:
                        gates["run_binding"] = _Gate("not_evaluated")
                    elif (
                        binding.workspace_id == workspace_id
                        and binding.user_id == actor.id
                    ):
                        gates["run_binding"] = _Gate("pass")
                    else:
                        gates["run_binding"] = _Gate(
                            "fail", "run_binding_ownership_mismatch"
                        )
                if gates["workspace_affinity"].status == "not_evaluated":
                    gates["workspace_affinity"] = _Gate("pass")

                if gates["active_verified_user"].status == "pass":
                    try:
                        await require_workspace_membership(
                            self.session,
                            actor,
                            workspace_id,
                        )
                    except IntelligenceAccessError:
                        gates["workspace_membership"] = _Gate(
                            "fail", "workspace_membership_missing"
                        )
                    else:
                        gates["workspace_membership"] = _Gate("pass")
                else:
                    gates["workspace_membership"] = _Gate("not_evaluated")

                if (
                    gates["workspace_membership"].status == "pass"
                    and gates["active_agent"].status == "pass"
                    and agent is not None
                ):
                    try:
                        await AgentGrantService(self.session).require(
                            workspace_id=workspace_id,
                            agent_id=agent.id,
                        )
                    except IntelligenceAccessError:
                        gates["workspace_agent_grant"] = _Gate(
                            "fail", "workspace_agent_grant_missing_or_revoked"
                        )
                    else:
                        gates["workspace_agent_grant"] = _Gate("pass")
                else:
                    gates["workspace_agent_grant"] = _Gate("not_evaluated")
        else:
            gates["active_agent"] = _Gate("not_evaluated")
            gates["workspace_affinity"] = _Gate("not_evaluated")
            gates["workspace_membership"] = _Gate("not_evaluated")
            gates["workspace_agent_grant"] = _Gate("not_evaluated")

        gates["can_invoke_agents"] = (
            _Gate("pass")
            if can_invoke_agents(actor)
            else _Gate("fail", "non_invoking_role")
        )

        first_gate, first_failure = self._first_failure(gates)
        authorized = first_failure is None
        record_audit(
            self.session,
            actor_id=operator.id,
            action=AuditAction.READ,
            entity=AuditEntity.VOICE_AUTHORIZATION_DIAGNOSTIC,
            entity_id=voice_session.id,
        )
        await self.session.commit()

        return VoiceAuthorizationDiagnostic(
            authorized=authorized,
            first_failing_gate=first_gate,
            first_failing_reason=(
                first_failure.reason if first_failure is not None else None
            ),
            actor_active=actor.is_active,
            actor_verified=actor.is_verified,
            actor_roles=sorted(user_roles(actor)),
            gates=VoiceAuthorizationGates(
                **{name: gate.status for name, gate in gates.items()}
            ),
        )

    @staticmethod
    def _active_verified_gate(actor: User) -> _Gate:
        if not actor.is_active:
            return _Gate("fail", "actor_inactive")
        if not actor.is_verified:
            return _Gate("fail", "actor_unverified")
        return _Gate("pass")

    @staticmethod
    def _session_owner_gate(subject_id: UUID, actor_id: UUID) -> _Gate:
        return (
            _Gate("pass")
            if subject_id == actor_id
            else _Gate("fail", "voice_session_owner_mismatch")
        )

    @staticmethod
    def _evaluate_run_gates(
        gates: dict[str, _Gate],
        *,
        conversation: AgentConversation,
        run: AgentRun | None,
        actor: User,
        workspace_id: UUID,
    ) -> None:
        if run is None:
            gates["workspace_affinity"] = _Gate("not_evaluated")
            return
        gates["run_triggered_by_actor"] = _Gate(
            "pass"
            if run.triggered_by_id == actor.id
            else "fail",
            None
            if run.triggered_by_id == actor.id
            else "run_actor_mismatch",
        )
        gates["run_agent_match"] = _Gate(
            "pass"
            if conversation.agent_id == run.agent_id
            else "fail",
            None
            if conversation.agent_id == run.agent_id
            else "run_agent_mismatch",
        )
        try:
            require_workspace_affinity(conversation, run, workspace_id)
        except IntelligenceAccessError:
            gates["workspace_affinity"] = _Gate(
                "fail", "workspace_affinity_mismatch"
            )
            gates["run_workspace_affinity"] = _Gate(
                "fail", "run_workspace_affinity_mismatch"
            )
        else:
            gates["run_workspace_affinity"] = _Gate("pass")

    @staticmethod
    def _first_failure(
        gates: dict[str, _Gate],
    ) -> tuple[str | None, _Gate | None]:
        order = (
            "active_verified_user",
            "voice_session_owner",
            "conversation_owner",
            "active_agent",
            "workspace_affinity",
            "workspace_membership",
            "workspace_agent_grant",
            "can_invoke_agents",
            "run_triggered_by_actor",
            "run_agent_match",
            "run_workspace_affinity",
            "run_binding",
        )
        for name in order:
            gate = gates.get(name)
            if gate is not None and gate.status == "fail":
                return name, gate
        return None, None
