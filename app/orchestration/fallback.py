from collections.abc import Awaitable, Callable
from typing import TypeVar

from app.orchestration.exceptions import OrchestrationFallbackExhausted
from app.core.redaction import safe_failure_detail
from app.orchestration.health import HealthContract
from app.orchestration.policy import OrchestrationPolicy
from app.providers.base import ProviderAdapter

ResultT = TypeVar("ResultT")


class OrchestrationFallback(HealthContract):
    component_name = "fallback"

    def __init__(self, policy: OrchestrationPolicy | None = None) -> None:
        self.policy = policy or OrchestrationPolicy()

    async def retry(
        self, operation: Callable[[], Awaitable[ResultT]]
    ) -> tuple[ResultT, int]:
        last_error: Exception | None = None
        for retry in range(self.policy.maximum_retries + 1):
            try:
                return await operation(), retry
            except Exception as error:
                last_error = error
        raise OrchestrationFallbackExhausted(
            safe_failure_detail(
                "Orchestration fallback exhausted",
                last_error or RuntimeError("Fallback exhausted"),
            )
        ) from last_error

    @staticmethod
    def graceful(error: Exception) -> dict[str, object]:
        return {
            "degraded": True,
            "error": type(error).__name__,
            "message": safe_failure_detail(
                "Orchestration action failed", error
            ),
        }

    @staticmethod
    async def provider_fallback(
        providers: tuple[ProviderAdapter, ...],
    ) -> ProviderAdapter:
        for provider in providers:
            if (await provider.health()).available:
                return provider
        raise OrchestrationFallbackExhausted(
            "No fallback provider is available"
        )

    @staticmethod
    def model_fallback(
        preferred: str,
        alternatives: tuple[str, ...],
        available: frozenset[str],
    ) -> str:
        for model in (preferred, *alternatives):
            if model in available:
                return model
        raise OrchestrationFallbackExhausted(
            "No fallback model is available"
        )

    @staticmethod
    def tool_fallback(
        preferred: str,
        alternatives: tuple[str, ...],
        available: frozenset[str],
    ) -> str:
        for tool in (preferred, *alternatives):
            if tool in available:
                return tool
        raise OrchestrationFallbackExhausted(
            "No fallback tool is available"
        )
