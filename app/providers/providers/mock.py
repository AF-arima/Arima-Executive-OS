from collections.abc import AsyncIterator
from decimal import Decimal

from app.providers.base import ProviderAdapter
from app.providers.config import ProviderConfig
from app.providers.exceptions import ProviderConfigurationError
from app.providers.types import (
    CompletionRequest,
    CompletionResponse,
    EmbeddingRequest,
    EmbeddingResponse,
    EstimatedCost,
    ModelInfo,
    PricingInfo,
    ProviderHealth,
    ProviderName,
    ProviderStatus,
    StreamChunk,
    TokenUsage,
)


class MockProvider(ProviderAdapter):
    def __init__(self, config: ProviderConfig) -> None:
        if config.provider is not ProviderName.MOCK:
            raise ProviderConfigurationError(
                "MockProvider requires mock configuration"
            )
        self.config = config
        self._model_info = ModelInfo(
            provider=ProviderName.MOCK,
            model=config.default_model,
            display_name="Deterministic Mock Model",
            context_window=config.max_model_tokens,
            max_output_tokens=config.max_output_tokens,
            capabilities=config.capabilities,
            pricing=PricingInfo(source="deterministic_zero_cost"),
        )

    @property
    def provider(self) -> ProviderName:
        return ProviderName.MOCK

    @property
    def models(self) -> tuple[str, ...]:
        return (self._model_info.model,)

    async def health(self) -> ProviderHealth:
        return ProviderHealth(
            provider=self.provider,
            status=ProviderStatus.HEALTHY,
            available=True,
            latency_ms=0,
            message="Deterministic mock provider is available.",
        )

    async def complete(
        self,
        request: CompletionRequest,
    ) -> CompletionResponse:
        self._validate_request(request)
        last_message = request.messages[-1]
        content = f"Mock response: {last_message.content or '[image]'}"
        usage = TokenUsage(
            input_tokens=sum(
                self.count_tokens(message.content, model=request.model)
                for message in request.messages
            ),
            output_tokens=self.count_tokens(content, model=request.model),
        )
        return CompletionResponse(
            provider=self.provider,
            model=request.model,
            content=content,
            usage=usage,
            estimated_cost=self.estimate_cost(usage, model=request.model),
            finish_reason="mock_complete",
            metadata={"deterministic": True, "network_used": False},
        )

    async def stream(
        self,
        request: CompletionRequest,
    ) -> AsyncIterator[StreamChunk]:
        self._validate_request(request)
        response = await self.complete(request)
        words = response.content.split()
        for index, word in enumerate(words):
            suffix = "" if index == len(words) - 1 else " "
            yield StreamChunk(
                provider=self.provider,
                model=request.model,
                index=index,
                content=f"{word}{suffix}",
                finished=index == len(words) - 1,
            )

    async def embeddings(
        self,
        request: EmbeddingRequest,
    ) -> EmbeddingResponse:
        self.require_model(request.model)
        if not self._model_info.capabilities.embeddings:
            raise ProviderConfigurationError(
                "Mock model does not support embeddings"
            )
        vectors = tuple(self._vector(item) for item in request.inputs)
        usage = TokenUsage(
            input_tokens=sum(
                self.count_tokens(item, model=request.model)
                for item in request.inputs
            ),
            output_tokens=0,
        )
        return EmbeddingResponse(
            provider=self.provider,
            model=request.model,
            vectors=vectors,
            usage=usage,
        )

    def count_tokens(self, text: str, *, model: str) -> int:
        self.require_model(model)
        return max(1, len(text.split())) if text else 0

    def estimate_cost(
        self,
        usage: TokenUsage,
        *,
        model: str,
    ) -> EstimatedCost:
        self.require_model(model)
        del usage
        return EstimatedCost(
            input_cost=Decimal("0"),
            output_cost=Decimal("0"),
            total_cost=Decimal("0"),
        )

    def model_information(self, model: str) -> ModelInfo:
        self.require_model(model)
        return self._model_info

    def _validate_request(self, request: CompletionRequest) -> None:
        information = self.model_information(request.model)
        if request.tools and not information.capabilities.tools:
            raise ProviderConfigurationError(
                "Mock model does not support tools"
            )
        if request.json_mode and not information.capabilities.json_mode:
            raise ProviderConfigurationError(
                "Mock model does not support JSON mode"
            )
        if (
            any(message.images for message in request.messages)
            and not self.supports_images(request.model)
        ):
            raise ProviderConfigurationError(
                "Mock model does not support images"
            )

    @staticmethod
    def _vector(text: str) -> tuple[float, ...]:
        codepoints = [ord(character) for character in text]
        total = sum(codepoints)
        weighted = sum(
            (index + 1) * value
            for index, value in enumerate(codepoints)
        )
        return (
            round((total % 997) / 997, 6),
            round((weighted % 991) / 991, 6),
            round((len(text) % 127) / 127, 6),
            1.0,
        )
