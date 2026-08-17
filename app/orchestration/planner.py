from __future__ import annotations

from datetime import datetime, timezone
import re

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
        live_step = self._live_data_step(content)
        if live_step is not None:
            steps.append(live_step)
        elif intent is OrchestrationIntent.PROJECTS:
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

    @staticmethod
    def _live_data_step(content: str) -> PlanStep | None:
        value = content.casefold()
        if "date" in value and ("day" in value or "today" in value):
            return PlanStep(
                target=PlanTarget.TOOL,
                name="runtime.current_date",
            )
        if any(term in value for term in ("bitcoin", "btc")) and any(
            term in value for term in ("price", "worth", "quote")
        ):
            return PlanStep(target=PlanTarget.TOOL, name="market.current_price", payload={"instrument": "BTCUSD"})
        if any(term in value for term in ("gold", "xau")) and any(
            term in value for term in ("price", "worth", "quote")
        ):
            return PlanStep(target=PlanTarget.TOOL, name="market.current_price", payload={"instrument": "XAUUSD"})
        if "weather" not in value:
            return None
        match = re.search(
            r"\b(?:in|at|for)\s+([a-z][a-z .,'-]{1,100})",
            content,
            re.IGNORECASE,
        )
        location = match.group(1).strip(" .,?!") if match else None
        if location is not None:
            location = re.sub(
                r"\s+\b(?:today|now|currently|right now)\b.*$",
                "",
                location,
                flags=re.IGNORECASE,
            ).strip(" .,?!") or None
        return PlanStep(target=PlanTarget.TOOL, name="weather.current", payload={"location": location})
