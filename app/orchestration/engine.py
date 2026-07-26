from app.orchestration.context import OrchestrationExecutionContext
from app.orchestration.pipeline import OrchestrationPipeline
from app.orchestration.schemas import OrchestrationResult


class OrchestrationEngine:
    def __init__(self, pipeline: OrchestrationPipeline) -> None:
        self.pipeline = pipeline

    async def execute(
        self, context: OrchestrationExecutionContext
    ) -> OrchestrationResult:
        return await self.pipeline.execute(context)

    async def health(self):
        return await self.pipeline.health()
