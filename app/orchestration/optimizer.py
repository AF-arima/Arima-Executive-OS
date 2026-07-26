from app.orchestration.health import HealthContract
from app.orchestration.schemas import (
    ModelProfile,
    OrchestrationIntent,
    OrchestrationRequest,
)


class OrchestrationOptimizer(HealthContract):
    component_name = "optimizer"

    def model_profile(
        self,
        request: OrchestrationRequest,
        intent: OrchestrationIntent,
    ) -> ModelProfile:
        if request.has_images:
            return ModelProfile.VISION_READY
        if request.require_json:
            return ModelProfile.JSON_READY
        if intent in {
            OrchestrationIntent.ANALYSIS,
            OrchestrationIntent.QUANT,
            OrchestrationIntent.PLANNING,
        }:
            return ModelProfile.REASONING
        if request.max_context_tokens > 32000:
            return ModelProfile.LONG_CONTEXT
        return request.model_profile
