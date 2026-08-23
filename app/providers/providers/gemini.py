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


class GeminiProvider(ProviderAdapter):
    """Server-side Gemini generateContent adapter for governed Arima runs."""

    api_base = "https://generativelanguage.googleapis.com/v1beta/models"
    user_agent = "arima-executive-os/0.1"

    def __init__(
        self,
        config: ProviderConfig,
        *,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 60.0,
    ) -> None:
        if config.provider is not ProviderName.GEMINI:
            raise ProviderConfigurationError(
                "GeminiProvider requires gemini configuration"
            )
        self.config = config
        self._client = client
        self._timeout_seconds = timeout_seconds
        self._model_info = ModelInfo(
            provider=config.provider,
            model=config.default_model,
            display_name="Google Gemini API",
            context_window=config.max_model_tokens,
            max_output_tokens=config.max_output_tokens,
            capabilities=config.capabilities,
        )

    @property
    def provider(self) -> ProviderName:
        return ProviderName.GEMINI

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
                "Gemini provider is configured."
                if configured
                else "Gemini provider credentials are unavailable."
            ),
        )

    async def complete(
        self,
        request: CompletionRequest,
    ) -> CompletionResponse:
        self._validate_request(request)
        headers = {
            "x-goog-api-key": self._api_key(),
            "Content-Type": "application/json",
            "User-Agent": self.user_agent,
        }
        payload = self._payload(request)
        started = perf_counter()
        if self._client is not None:
            response = await self._post(self._client, headers, payload, request)
        else:
            async with httpx.AsyncClient(
                timeout=self._timeout_seconds
            ) as client:
                response = await self._post(client, headers, payload, request)
        latency_ms = max(int((perf_counter() - started) * 1_000), 0)
        body = self._response_body(response)
        content, finish_reason = self._content(body)
        usage = self._usage(body)
        return CompletionResponse(
            provider=self.provider,
            model=request.model,
            content=content,
            usage=usage,
            estimated_cost=self.estimate_cost(usage, model=request.model),
            finish_reason=finish_reason,
            metadata={"latency_ms": latency_ms, "stored_by_provider": False},
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
            "Gemini embeddings are not enabled for this adapter"
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
                "Gemini provider credentials are unavailable"
            )
        return value

    def _validate_request(self, request: CompletionRequest) -> None:
        self.require_model(request.model)
        if request.tools:
            raise ProviderConfigurationError(
                "Gemini provider tool calling is not enabled"
            )
        if request.json_mode:
            raise ProviderConfigurationError(
                "Gemini provider JSON mode is not enabled"
            )
        if any(message.images for message in request.messages):
            raise ProviderConfigurationError(
                "Gemini provider image input is not enabled"
            )

    @staticmethod
    def _payload(request: CompletionRequest) -> dict[str, object]:
        system = [
            message.content
            for message in request.messages
            if message.role.value == "system"
        ]
        contents = [
            {
                "role": "model" if message.role.value == "assistant" else "user",
                "parts": [{"text": message.content}],
            }
            for message in request.messages
            if message.role.value != "system"
        ]
        payload: dict[str, object] = {
            "contents": contents,
            "generationConfig": {
                "temperature": request.temperature,
                "maxOutputTokens": request.max_output_tokens,
            },
        }
        if system:
            payload["systemInstruction"] = {
                "parts": [{"text": "\n".join(system)}]
            }
        return payload

    async def _post(
        self,
        client: httpx.AsyncClient,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
        request: CompletionRequest,
    ) -> httpx.Response:
        try:
            response = await client.post(
                f"{self.api_base}/{request.model}:generateContent",
                headers=headers,
                json=payload,
            )
        except httpx.TimeoutException as error:
            raise ProviderTimeout("Gemini request timed out") from error
        except httpx.HTTPError as error:
            raise ProviderUnavailable("Gemini request failed") from error
        if response.status_code in {401, 403}:
            raise AuthenticationFailure("Gemini provider authentication failed")
        if response.status_code == 429:
            raise RateLimitExceeded("Gemini provider rate limit exceeded")
        if not response.is_success:
            raise ProviderUnavailable(
                "Gemini provider rejected the request "
                f"(HTTP {response.status_code})"
            )
        return response

    @staticmethod
    def _response_body(response: httpx.Response) -> dict[str, Any]:
        try:
            body = response.json()
        except ValueError as error:
            raise ProviderUnavailable("Gemini provider returned invalid JSON") from error
        if not isinstance(body, dict):
            raise ProviderUnavailable("Gemini provider returned invalid JSON")
        return body

    @staticmethod
    def _content(body: Mapping[str, Any]) -> tuple[str, str]:
        candidates = body.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            raise ProviderUnavailable("Gemini provider returned no usable output")
        candidate = candidates[0]
        if not isinstance(candidate, dict):
            raise ProviderUnavailable("Gemini provider returned no usable output")
        content = candidate.get("content")
        parts = content.get("parts") if isinstance(content, dict) else None
        if not isinstance(parts, list):
            raise ProviderUnavailable("Gemini provider returned no usable output")
        text = "".join(
            part.get("text", "")
            for part in parts
            if isinstance(part, dict)
            and part.get("thought") is not True
            and isinstance(part.get("text"), str)
        ).strip()
        if not text:
            raise ProviderUnavailable("Gemini provider returned no usable output")
        finish_reason = candidate.get("finishReason")
        return text, finish_reason if isinstance(finish_reason, str) else "completed"

    @staticmethod
    def _usage(body: Mapping[str, Any]) -> TokenUsage:
        usage = body.get("usageMetadata")
        if not isinstance(usage, dict):
            return TokenUsage(input_tokens=0, output_tokens=0)
        input_tokens = usage.get("promptTokenCount", 0)
        output_tokens = usage.get("candidatesTokenCount", 0)
        return TokenUsage(
            input_tokens=input_tokens if isinstance(input_tokens, int) else 0,
            output_tokens=output_tokens if isinstance(output_tokens, int) else 0,
        )
