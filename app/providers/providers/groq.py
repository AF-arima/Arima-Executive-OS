from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
import json
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
    MessageRole,
    ModelInfo,
    ProviderHealth,
    ProviderMessage,
    ProviderName,
    ProviderStatus,
    ProviderToolCall,
    StreamChunk,
    TokenUsage,
)
from app.voice.observability import (
    http_status_category,
    normalized_failure_class,
    observer_from_metadata,
)


class GroqProvider(ProviderAdapter):
    """Groq OpenAI-compatible Chat Completions adapter."""

    api_path = "/chat/completions"
    user_agent = "arima-executive-os/0.1"
    _finish_reasons = frozenset({"stop", "length", "tool_calls"})

    def __init__(
        self,
        config: ProviderConfig,
        *,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 60.0,
    ) -> None:
        if config.provider is not ProviderName.GROQ:
            raise ProviderConfigurationError(
                "GroqProvider requires groq configuration"
            )
        if not config.base_url:
            raise ProviderConfigurationError("Groq API base URL is required")
        self.config = config
        self._client = client
        self._timeout_seconds = timeout_seconds
        self.api_url = config.base_url.rstrip("/") + self.api_path
        self._model_info = ModelInfo(
            provider=config.provider,
            model=config.default_model,
            display_name="Groq Chat Completions API",
            context_window=config.max_model_tokens,
            max_output_tokens=config.max_output_tokens,
            capabilities=config.capabilities,
        )

    @property
    def provider(self) -> ProviderName:
        return ProviderName.GROQ

    @property
    def models(self) -> tuple[str, ...]:
        return (self._model_info.model,)

    async def health(self) -> ProviderHealth:
        configured = bool(self._api_key(required=False))
        return ProviderHealth(
            provider=self.provider,
            status=ProviderStatus.HEALTHY if configured else ProviderStatus.UNAVAILABLE,
            available=configured,
            latency_ms=0,
            message=(
                "Groq provider is configured."
                if configured
                else "Groq provider credentials are unavailable."
            ),
        )

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        self._validate_request(request)
        observer = observer_from_metadata(request.metadata)
        raw_attempt = request.metadata.get("provider_attempt")
        attempt = raw_attempt if isinstance(raw_attempt, int) else None
        diagnostics = self._diagnostic_fields(request)
        started = perf_counter()
        if observer is not None:
            observer.emit(
                "provider_attempt_start",
                attempt=attempt,
                provider=self.provider.value,
                outcome="started",
                provider_timeout_ms=self._timeout_seconds * 1000,
                **diagnostics,
            )
            observer.emit(
                "provider_request_dispatched",
                attempt=attempt,
                provider=self.provider.value,
                outcome="dispatched",
                **diagnostics,
            )
        headers = {
            "Authorization": f"Bearer {self._api_key()}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": self.user_agent,
        }
        payload: dict[str, object] = {
            "model": request.model,
            "messages": [self._message_payload(message) for message in request.messages],
            "max_completion_tokens": min(
                request.max_output_tokens,
                self.config.max_output_tokens,
            ),
            "temperature": request.temperature,
            "stream": False,
            "include_reasoning": False,
        }
        if request.tools:
            payload["tools"] = list(request.tools)
        if request.json_mode:
            payload["response_format"] = {"type": "json_object"}
        try:
            if self._client is not None:
                response = await self._post(self._client, headers, payload)
            else:
                async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                    response = await self._post(client, headers, payload)
            if observer is not None:
                observer.emit(
                    "provider_response_received",
                    attempt=attempt,
                    provider=self.provider.value,
                    outcome="received",
                    status_code=response.status_code,
                    status_category=http_status_category(response.status_code),
                    duration_ms=round((perf_counter() - started) * 1000, 2),
                    **diagnostics,
                )
            body = self._response_body(response)
            content, finish_reason, tool_calls = self._completion(body)
            usage = self._usage(body)
            result = CompletionResponse(
                provider=self.provider,
                model=(body.get("model") if isinstance(body.get("model"), str) else request.model),
                content=content,
                usage=usage,
                estimated_cost=self.estimate_cost(usage, model=request.model),
                finish_reason=finish_reason,
                tool_calls=tool_calls,
                metadata={"response_id": body.get("id") if isinstance(body.get("id"), str) else None},
            )
            if observer is not None:
                observer.emit(
                    "provider_attempt_success",
                    attempt=attempt,
                    provider=self.provider.value,
                    outcome="success",
                    duration_ms=round((perf_counter() - started) * 1000, 2),
                    **diagnostics,
                )
            return result
        except Exception as error:
            if observer is not None:
                observer.emit(
                    "provider_attempt_failure",
                    attempt=attempt,
                    provider=self.provider.value,
                    outcome="failed",
                    failure_class=normalized_failure_class(error),
                    exception_type=type(error).__name__,
                    duration_ms=round((perf_counter() - started) * 1000, 2),
                    status_code=getattr(error, "status_code", None),
                    status_category=http_status_category(getattr(error, "status_code", None)),
                    **diagnostics,
                )
            raise

    async def stream(self, request: CompletionRequest) -> AsyncIterator[StreamChunk]:
        response = await self.complete(request)
        words = response.content.split()
        for index, word in enumerate(words):
            yield StreamChunk(
                provider=response.provider,
                model=response.model,
                index=index,
                content=word + (" " if index < len(words) - 1 else ""),
                finished=index == len(words) - 1,
            )

    async def embeddings(self, request: EmbeddingRequest) -> EmbeddingResponse:
        self.require_model(request.model)
        raise ProviderConfigurationError("Groq embeddings are not enabled")

    def count_tokens(self, text: str, *, model: str) -> int:
        self.require_model(model)
        return max(1, len(text.split())) if text else 0

    def model_information(self, model: str) -> ModelInfo:
        self.require_model(model)
        return self._model_info

    def _api_key(self, *, required: bool = True) -> str:
        value = self.config.api_key.get_secret_value().strip() if self.config.api_key else ""
        if required and not value:
            raise ProviderConfigurationError("Groq provider credentials are unavailable")
        return value

    def _validate_request(self, request: CompletionRequest) -> None:
        self.require_model(request.model)
        if any(message.images for message in request.messages):
            raise ProviderConfigurationError("Groq text API does not support images")

    @staticmethod
    def _diagnostic_fields(request: CompletionRequest) -> dict[str, object]:
        return {
            key: request.metadata[key]
            for key in ("request_mode", "response_language")
            if isinstance(request.metadata.get(key), str)
        }

    async def _post(
        self,
        client: httpx.AsyncClient,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
    ) -> httpx.Response:
        try:
            response = await client.post(self.api_url, headers=headers, json=payload)
        except httpx.TimeoutException as error:
            raise ProviderTimeout("Groq request timed out") from error
        except httpx.HTTPError as error:
            raise ProviderUnavailable("Groq request failed") from error
        if response.status_code in {401, 403}:
            raise AuthenticationFailure("Groq provider authentication failed")
        if response.status_code == 408:
            raise ProviderTimeout("Groq request timed out")
        if response.status_code == 429:
            raise RateLimitExceeded("Groq provider rate limit exceeded")
        if response.status_code != 200:
            raise ProviderUnavailable("Groq provider rejected the request")
        return response

    @staticmethod
    def _response_body(response: httpx.Response) -> dict[str, Any]:
        try:
            body = response.json()
        except ValueError as error:
            raise ProviderUnavailable("Groq provider returned invalid JSON") from error
        if not isinstance(body, dict):
            raise ProviderUnavailable("Groq provider returned an invalid response")
        return body

    @classmethod
    def _completion(cls, body: Mapping[str, Any]) -> tuple[str, str, tuple[ProviderToolCall, ...]]:
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise ProviderUnavailable("Groq provider returned no usable output")
        choice = choices[0]
        message = choice.get("message")
        if not isinstance(message, dict):
            raise ProviderUnavailable("Groq provider returned no usable message")
        content = message.get("content") or ""
        if not isinstance(content, str):
            raise ProviderUnavailable("Groq provider returned invalid content")
        tool_calls = cls._tool_calls(message.get("tool_calls"))
        finish_reason = choice.get("finish_reason")
        if finish_reason not in cls._finish_reasons:
            raise ProviderUnavailable("Groq provider returned an invalid finish reason")
        if finish_reason == "tool_calls" and not tool_calls:
            raise ProviderUnavailable("Groq provider returned empty tool calls")
        if finish_reason in {"stop", "length"} and not content.strip():
            raise ProviderUnavailable("Groq provider returned empty output")
        return content.strip(), finish_reason, tool_calls

    @staticmethod
    def _tool_calls(raw: object) -> tuple[ProviderToolCall, ...]:
        if raw is None:
            return ()
        if not isinstance(raw, list):
            raise ProviderUnavailable("Groq provider returned malformed tool calls")
        result = []
        for item in raw:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                raise ProviderUnavailable("Groq provider returned malformed tool call")
            function = item.get("function")
            if not isinstance(function, dict) or not isinstance(function.get("name"), str):
                raise ProviderUnavailable("Groq provider returned malformed tool call")
            arguments = function.get("arguments", "{}")
            if not isinstance(arguments, str):
                raise ProviderUnavailable("Groq provider returned malformed tool arguments")
            try:
                decoded = json.loads(arguments)
            except (TypeError, ValueError) as error:
                raise ProviderUnavailable("Groq provider returned invalid tool arguments") from error
            if not isinstance(decoded, dict):
                raise ProviderUnavailable("Groq provider tool arguments must be an object")
            result.append(ProviderToolCall(function["name"], item["id"], decoded))
        return tuple(result)

    @staticmethod
    def _message_payload(message: ProviderMessage) -> dict[str, object]:
        if message.role is MessageRole.TOOL:
            result = message.tool_result
            if result is None:
                raise ProviderConfigurationError("Groq tool message is missing its result")
            return {"role": "tool", "tool_call_id": result.call_id, "content": result.serialized_result}
        payload: dict[str, object] = {"role": message.role.value, "content": message.content}
        if message.tool_calls:
            payload["tool_calls"] = [{
                "id": call.call_id,
                "type": "function",
                "function": {"name": call.wire_name, "arguments": json.dumps(call.arguments, separators=(",", ":"))},
            } for call in message.tool_calls]
        return payload

    @staticmethod
    def _usage(body: Mapping[str, Any]) -> TokenUsage:
        usage = body.get("usage")
        if not isinstance(usage, dict):
            raise ProviderUnavailable("Groq provider returned invalid token usage")
        prompt_tokens = usage.get("prompt_tokens")
        completion_tokens = usage.get("completion_tokens")
        if (
            not isinstance(prompt_tokens, int) or isinstance(prompt_tokens, bool)
            or not isinstance(completion_tokens, int) or isinstance(completion_tokens, bool)
            or prompt_tokens < 0 or completion_tokens < 0
        ):
            raise ProviderUnavailable("Groq provider returned invalid token usage")
        return TokenUsage(prompt_tokens, completion_tokens)
