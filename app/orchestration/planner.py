from __future__ import annotations

from datetime import datetime, timezone

from app.orchestration.health import HealthContract
from app.orchestration.policy import OrchestrationPolicy
from app.orchestration.schemas import (
    ExecutionPlan,
    OrchestrationIntent,
    PlanMode,
    PlanStep,
    PlanTarget,
)


class OrchestrationPlanner(HealthContract):
    component_name = "planner"

    def __init__(self, policy: OrchestrationPolicy | None = None) -> None:
        self.policy = policy or OrchestrationPolicy()

    def plan(
        self,
        intent: OrchestrationIntent,
        content: str,
        *,
        mode: PlanMode = PlanMode.SEQUENTIAL,
    ) -> ExecutionPlan:
        steps: list[PlanStep] = []
        if intent is OrchestrationIntent.PROJECTS:
            steps.append(
                PlanStep(target=PlanTarget.TOOL, name="project.analytics")
            )
        elif intent is OrchestrationIntent.PORTFOLIO:
            steps.append(
                PlanStep(target=PlanTarget.TOOL, name="portfolio.summary")
            )
        elif intent is OrchestrationIntent.TASK:
            steps.append(
                PlanStep(target=PlanTarget.TOOL, name="task.search")
            )
        elif intent is OrchestrationIntent.SEARCH:
            steps.append(
                PlanStep(
                    target=PlanTarget.INTEGRATION,
                    name="search",
                    operation="web_search",
                    payload={"query": content},
                )
            )
        elif intent is OrchestrationIntent.QUANT:
            steps.append(
                PlanStep(
                    target=PlanTarget.BACKGROUND,
                    name="quant_research_summary",
                )
            )
        elif intent is OrchestrationIntent.EXECUTION:
            steps.append(
                PlanStep(target=PlanTarget.AGENT, name="mock")
            )
        steps.append(PlanStep(target=PlanTarget.RESPONSE, name="assemble"))
        return ExecutionPlan(
            intent=intent,
            mode=mode,
            steps=steps,
            policies=self.policy.execution_policies,
            created_at=datetime.now(timezone.utc),
        )
