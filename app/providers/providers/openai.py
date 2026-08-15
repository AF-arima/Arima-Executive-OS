from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from time import perf_counter
from typing import Any

import httpx

from app.providers.base import ProviderAdapter
from app.providers.config import ProviderConfig
from app.providers.exceptions import (
    AuthenticationFailure,
    ProviderConfigurationError,
    ProviderTimeout,
    ProviderUnavailable,
    RateLimitExceeded,
)
from app.providers.types import (
    CompletionRequest,
    CompletionResponse,
    EmbeddingRequest,
    EmbeddingResponse,
    ModelInfo,
    ProviderHealth,
    ProviderName,
    ProviderStatus,
    StreamChunk,
    TokenUsage,
)


class OpenAIProvider(ProviderAdapter):
    """Server-side OpenAI Responses API adapter for governed Arima runs."""

    api_url = "https://api.openai.com/v1/responses"
    user_agent = "arima-executive-os/0.1"

    def __init__(
        self,
        config: ProviderConfig,
        *,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 60.0,
    ) -> None:
        if config.provider is not ProviderName.OPENAI:
            raise ProviderConfigurationError(
                "OpenAIProvider requires openai configuration"
            )
        self.config = config
        self._client = client
        self._timeout_seconds = timeout_seconds
        self._model_info = ModelInfo(
            provider=config.provider,
            model=config.default_model,
            display_name="OpenAI Responses API",
            context_window=config.max_model_tokens,
            max_output_tokens=config.max_output_tokens,
            capabilities=config.capabilities,
        )

    @property
    def provider(self) -> ProviderName:
        return ProviderName.OPENAI

    @property
    def models(self) -> tuple[str, ...]:
        return (self._model_info.model,)

    async def health(self) -> ProviderHealth:
        configured = bool(self._api_key(required=False))
        return ProviderHealth(
            provider=self.provider,
            status=(
                ProviderStatus.HEALTHY
                if configured
                else ProviderStatus.UNAVAILABLE
            ),
            available=configured,
            latency_ms=0,
            message=(
                "OpenAI provider is configured."
                if configured
                else "OpenAI provider credentials are unavailable."
            ),
        )

    async def complete(
        self,
        request: CompletionRequest,
    ) -> CompletionResponse:
        self._validate_request(request)
        headers = {
            "Authorization": f"Bearer {self._api_key()}",
            "Content-Type": "application/json",
            "User-Agent": self.user_agent,
        }
        payload: dict[str, object] = {
            "model": request.model,
            "input": [
                {"role": message.role.value, "content": message.content}
                for message in request.messages
            ],
            "max_output_tokens": min(
                request.max_output_tokens,
                self.config.max_output_tokens,
            ),
            # Arima owns the durable conversation, provenance, and audit chain.
            "store": False,
        }
        started = perf_counter()
        if self._client is not None:
            response = await self._post(self._client, headers, payload)
        else:
            async with httpx.AsyncClient(
                timeout=self._timeout_seconds
            ) as client:
                response = await self._post(client, headers, payload)
        latency_ms = max(int((perf_counter() - started) * 1_000), 0)
        body = self._response_body(response)
        if body.get("status") != "completed":
            raise ProviderUnavailable(
                "OpenAI provider did not complete the response"
            )
        content = self._output_text(body)
        usage = self._usage(body)
        response_id = body.get("id")
        response_model = body.get("model")
        return CompletionResponse(
            provider=self.provider,
            model=(
                response_model
                if isinstance(response_model, str) and response_model
                else request.model
            ),
            content=content,
            usage=usage,
            estimated_cost=self.estimate_cost(usage, model=request.model),
            finish_reason=self._finish_reason(body),
            metadata={
                "response_id": (
                    response_id if isinstance(response_id, str) else None
                ),
                "latency_ms": latency_ms,
                "stored_by_provider": False,
            },
        )

    async def stream(
        self,
        request: CompletionRequest,
    ) -> AsyncIterator[StreamChunk]:
        response = await self.complete(request)
        words = response.content.split()
        for index, word in enumerate(words):
            yield StreamChunk(
                provider=self.provider,
                model=request.model,
                index=index,
                content=word + (" " if index < len(words) - 1 else ""),
                finished=index == len(words) - 1,
            )

    async def embeddings(
        self,
        request: EmbeddingRequest,
    ) -> EmbeddingResponse:
        self.require_model(request.model)
        raise ProviderConfigurationError(
            "OpenAI embeddings are not enabled for this adapter"
        )

    def count_tokens(self, text: str, *, model: str) -> int:
        self.require_model(model)
        return max(1, len(text.split())) if text else 0

    def model_information(self, model: str) -> ModelInfo:
        self.require_model(model)
        return self._model_info

    def _api_key(self, *, required: bool = True) -> str:
        secret = self.config.api_key
        value = secret.get_secret_value().strip() if secret is not None else ""
        if required and not value:
            raise ProviderConfigurationError(
                "OpenAI provider credentials are unavailable"
            )
        return value

    def _validate_request(self, request: CompletionRequest) -> None:
        self.require_model(request.model)
        if request.tools:
            raise ProviderConfigurationError(
                "OpenAI provider tool calling is not enabled"
            )
        if request.json_mode:
            raise ProviderConfigurationError(
                "OpenAI provider JSON mode is not enabled"
            )
        if any(message.images for message in request.messages):
            raise ProviderConfigurationError(
                "OpenAI provider image input is not enabled"
            )
        if any(message.role.value == "tool" for message in request.messages):
            raise ProviderConfigurationError(
                "OpenAI provider tool messages are not enabled"
            )

    async def _post(
        self,
        client: httpx.AsyncClient,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
    ) -> httpx.Response:
        try:
            response = await client.post(
                self.api_url,
                headers=headers,
                json=payload,
            )
        except httpx.TimeoutException as error:
            raise ProviderTimeout("OpenAI request timed out") from error
        except httpx.HTTPError as error:
            raise ProviderUnavailable("OpenAI request failed") from error
        if response.status_code in {401, 403}:
            raise AuthenticationFailure(
                "OpenAI provider authentication failed"
            )
        if response.status_code == 429:
            raise RateLimitExceeded("OpenAI provider rate limit exceeded")
        if not response.is_success:
            raise ProviderUnavailable(
                f"OpenAI provider rejected the request (HTTP {response.status_code})"
            )
        return response

    @staticmethod
    def _response_body(response: httpx.Response) -> dict[str, Any]:
        try:
            body = response.json()
        except ValueError as error:
            raise ProviderUnavailable(
                "OpenAI provider returned an invalid response"
            ) from error
        if not isinstance(body, dict):
            raise ProviderUnavailable(
                "OpenAI provider returned an invalid response"
            )
        return body

    @staticmethod
    def _output_text(body: Mapping[str, Any]) -> str:
        output = body.get("output")
        if not isinstance(output, list):
            raise ProviderUnavailable(
                "OpenAI provider returned no usable output"
            )
        texts: list[str] = []
        for item in output:
            if not isinstance(item, dict) or item.get("type") != "message":
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                if (
                    isinstance(part, dict)
                    and part.get("type") == "output_text"
                    and isinstance(part.get("text"), str)
                ):
                    texts.append(part["text"])
        rendered = "".join(texts).strip()
        if not rendered:
            raise ProviderUnavailable(
                "OpenAI provider returned no usable output"
            )
        return rendered

    @staticmethod
    def _usage(body: Mapping[str, Any]) -> TokenUsage:
        usage = body.get("usage")
        if not isinstance(usage, dict):
            raise ProviderUnavailable(
                "OpenAI provider returned invalid token usage"
            )
        input_tokens = usage.get("input_tokens")
        output_tokens = usage.get("output_tokens")
        if (
            not isinstance(input_tokens, int)
            or isinstance(input_tokens, bool)
            or not isinstance(output_tokens, int)
            or isinstance(output_tokens, bool)
            or input_tokens < 0
            or output_tokens < 0
        ):
            raise ProviderUnavailable(
                "OpenAI provider returned invalid token usage"
            )
        return TokenUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    @staticmethod
    def _finish_reason(body: Mapping[str, Any]) -> str:
        status = body.get("status")
        return status if isinstance(status, str) and status else "completed"
