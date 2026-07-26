from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from decimal import Decimal, ROUND_HALF_UP

from app.providers.exceptions import InvalidModel, ProviderUnavailable
from app.providers.types import (
    CompletionRequest,
    CompletionResponse,
    EmbeddingRequest,
    EmbeddingResponse,
    EstimatedCost,
    ModelInfo,
    ProviderHealth,
    ProviderName,
    ProviderStatus,
    StreamChunk,
    TokenUsage,
)

GBP_QUANTUM = Decimal("0.000001")
MILLION_TOKENS = Decimal("1000000")


class ProviderAdapter(ABC):
    @property
    @abstractmethod
    def provider(self) -> ProviderName:
        """Stable provider identifier."""

    @property
    @abstractmethod
    def models(self) -> tuple[str, ...]:
        """Models exposed by this adapter instance."""

    @abstractmethod
    async def health(self) -> ProviderHealth:
        """Return provider availability without raising for normal outages."""

    @abstractmethod
    async def complete(
        self,
        request: CompletionRequest,
    ) -> CompletionResponse:
        """Produce one completion."""

    @abstractmethod
    def stream(
        self,
        request: CompletionRequest,
    ) -> AsyncIterator[StreamChunk]:
        """Produce completion chunks."""

    @abstractmethod
    async def embeddings(
        self,
        request: EmbeddingRequest,
    ) -> EmbeddingResponse:
        """Produce embedding vectors."""

    @abstractmethod
    def count_tokens(self, text: str, *, model: str) -> int:
        """Estimate tokens without making a provider request."""

    def estimate_cost(
        self,
        usage: TokenUsage,
        *,
        model: str,
    ) -> EstimatedCost:
        information = self.model_information(model)
        pricing = information.pricing
        if not pricing.configured:
            return EstimatedCost(
                input_cost=Decimal("0"),
                output_cost=Decimal("0"),
                total_cost=Decimal("0"),
            )
        if (
            pricing.input_per_million_tokens is None
            or pricing.output_per_million_tokens is None
        ):
            raise RuntimeError("Configured pricing is incomplete")
        input_cost = (
            Decimal(usage.input_tokens)
            / MILLION_TOKENS
            * pricing.input_per_million_tokens
        ).quantize(GBP_QUANTUM, rounding=ROUND_HALF_UP)
        output_cost = (
            Decimal(usage.output_tokens)
            / MILLION_TOKENS
            * pricing.output_per_million_tokens
        ).quantize(GBP_QUANTUM, rounding=ROUND_HALF_UP)
        return EstimatedCost(
            input_cost=input_cost,
            output_cost=output_cost,
            total_cost=input_cost + output_cost,
            currency=pricing.currency,
        )

    def supports_tools(self, model: str) -> bool:
        return self.model_information(model).capabilities.tools

    def supports_streaming(self, model: str) -> bool:
        return self.model_information(model).capabilities.streaming

    def supports_images(self, model: str) -> bool:
        capabilities = self.model_information(model).capabilities
        return capabilities.vision or capabilities.multimodal

    def supports_json_mode(self, model: str) -> bool:
        return self.model_information(model).capabilities.json_mode

    @abstractmethod
    def model_information(self, model: str) -> ModelInfo:
        """Return immutable model metadata."""

    def require_model(self, model: str) -> None:
        if model not in self.models:
            raise InvalidModel(
                f"Model {model!r} is not registered for {self.provider.value}"
            )


class ConfiguredProviderStub(ProviderAdapter):
    """Non-executing placeholder for a future concrete provider plug-in."""

    def __init__(self, model_info: ModelInfo) -> None:
        self._model_info = model_info

    @property
    def provider(self) -> ProviderName:
        return self._model_info.provider

    @property
    def models(self) -> tuple[str, ...]:
        return (self._model_info.model,)

    async def health(self) -> ProviderHealth:
        return ProviderHealth(
            provider=self.provider,
            status=ProviderStatus.UNAVAILABLE,
            available=False,
            latency_ms=None,
            message="Provider integration is not implemented.",
        )

    async def complete(
        self,
        request: CompletionRequest,
    ) -> CompletionResponse:
        self.require_model(request.model)
        raise ProviderUnavailable(
            f"{self.provider.value} execution is not implemented"
        )

    async def stream(
        self,
        request: CompletionRequest,
    ) -> AsyncIterator[StreamChunk]:
        self.require_model(request.model)
        raise ProviderUnavailable(
            f"{self.provider.value} streaming is not implemented"
        )
        if False:
            yield StreamChunk(
                provider=self.provider,
                model=request.model,
                index=0,
                content="",
            )

    async def embeddings(
        self,
        request: EmbeddingRequest,
    ) -> EmbeddingResponse:
        self.require_model(request.model)
        raise ProviderUnavailable(
            f"{self.provider.value} embeddings are not implemented"
        )

    def count_tokens(self, text: str, *, model: str) -> int:
        self.require_model(model)
        return max(1, len(text.split())) if text else 0

    def model_information(self, model: str) -> ModelInfo:
        self.require_model(model)
        return self._model_info
