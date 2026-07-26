from decimal import Decimal

from app.orchestration.exceptions import OrchestrationBudgetExceeded
from app.orchestration.health import HealthContract
from app.orchestration.schemas import OrchestrationCost
from app.providers.base import ProviderAdapter
from app.providers.types import TokenUsage


class OrchestrationCostEngine(HealthContract):
    component_name = "cost"

    def estimate(
        self,
        provider: ProviderAdapter,
        *,
        model: str,
        input_tokens: int,
        output_tokens: int,
        tool_count: int,
        integration_count: int,
        budget_gbp: Decimal,
    ) -> OrchestrationCost:
        usage = TokenUsage(input_tokens, output_tokens)
        provider_cost = provider.estimate_cost(usage, model=model).total_cost
        tool_cost = Decimal("0") * tool_count
        integration_cost = Decimal("0") * integration_count
        execution_cost = Decimal("0")
        total = (
            provider_cost
            + tool_cost
            + integration_cost
            + execution_cost
        )
        return OrchestrationCost(
            provider_cost_gbp=provider_cost,
            tool_cost_gbp=tool_cost,
            integration_cost_gbp=integration_cost,
            execution_cost_gbp=execution_cost,
            total_cost_gbp=total,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            within_budget=budget_gbp == 0 or total <= budget_gbp,
        )

    @staticmethod
    def require_budget(cost: OrchestrationCost) -> None:
        if not cost.within_budget:
            raise OrchestrationBudgetExceeded(
                "Orchestration budget would be exceeded"
            )
