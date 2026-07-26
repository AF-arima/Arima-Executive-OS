from app.background.registry import BackgroundJobRegistry
from app.orchestration.exceptions import OrchestrationApprovalRequired
from app.orchestration.health import HealthContract
from app.orchestration.schemas import (
    ApprovalRequirement,
    ExecutionPlan,
    PlanTarget,
)
from app.integrations.registry import ConnectorRegistry
from app.integrations.schemas import ApprovalPolicy


class OrchestrationApprovalEngine(HealthContract):
    component_name = "approval"

    def __init__(
        self,
        integrations: ConnectorRegistry,
        background: BackgroundJobRegistry,
    ) -> None:
        self.integrations = integrations
        self.background = background

    def evaluate(
        self,
        plan: ExecutionPlan,
        *,
        approved_steps: frozenset[str] = frozenset(),
    ) -> list[ApprovalRequirement]:
        requirements = []
        for step in plan.steps:
            policy = "none"
            if step.target is PlanTarget.INTEGRATION:
                connector = self.integrations.get(step.name)
                operation = next(
                    item
                    for item in connector.supported_operations()
                    if item.name == step.operation
                )
                policy = operation.approval_policy.value
            elif step.target is PlanTarget.BACKGROUND:
                policy = (
                    self.background.get(step.name)
                    .required_approval_policy()
                    .value
                )
            elif step.approval_checkpoint:
                policy = ApprovalPolicy.USER.value
            if policy != "none":
                requirements.append(
                    ApprovalRequirement(
                        step_id=step.id,
                        target=step.target,
                        policy=policy,
                        reason=f"{step.name} requires {policy} approval",
                        approved=str(step.id) in approved_steps,
                    )
                )
        return requirements

    @staticmethod
    def require(requirements: list[ApprovalRequirement]) -> None:
        if any(not requirement.approved for requirement in requirements):
            raise OrchestrationApprovalRequired(
                "The execution plan contains unapproved operations"
            )
