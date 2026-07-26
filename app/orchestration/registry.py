from collections.abc import Callable
from typing import Any

from app.orchestration.exceptions import OrchestrationConfigurationError
from app.orchestration.schemas import OrchestrationStage

StageHandler = Callable[..., Any]


class OrchestrationRegistry:
    def __init__(self) -> None:
        self._handlers: dict[OrchestrationStage, StageHandler] = {}

    def register(
        self,
        stage: OrchestrationStage,
        handler: StageHandler,
        *,
        replace: bool = False,
    ) -> None:
        if stage in self._handlers and not replace:
            raise OrchestrationConfigurationError(
                f"Orchestration stage already registered: {stage.value}"
            )
        self._handlers[stage] = handler

    def get(self, stage: OrchestrationStage) -> StageHandler:
        try:
            return self._handlers[stage]
        except KeyError as error:
            raise OrchestrationConfigurationError(
                f"Orchestration stage is not registered: {stage.value}"
            ) from error

    def stages(self) -> tuple[OrchestrationStage, ...]:
        return tuple(self._handlers)

    def __len__(self) -> int:
        return len(self._handlers)
