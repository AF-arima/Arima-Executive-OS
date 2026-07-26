from dataclasses import dataclass

from app.orchestration.schemas import ExecutionPolicy


@dataclass(frozen=True, slots=True)
class OrchestrationPolicy:
    execution_policies: frozenset[ExecutionPolicy] = frozenset(
        {
            ExecutionPolicy.SEQUENTIAL,
            ExecutionPolicy.PARALLEL_ABSTRACTION,
            ExecutionPolicy.CONDITIONAL,
            ExecutionPolicy.APPROVAL_CHECKPOINT,
            ExecutionPolicy.RETRY_CHECKPOINT,
        }
    )
    maximum_retries: int = 2
    graceful_degradation: bool = True

    def __post_init__(self) -> None:
        if self.maximum_retries < 0:
            raise ValueError("Maximum retries cannot be negative")
