from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
import asyncio
import json
import logging
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


logger = logging.getLogger("arima.provider.execution")

SAFETY_MARGIN_SECONDS = 2.0
MIN_TIMEOUT_SECONDS = 5.0


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
            request_timeout = self._request_timeout(request)
            if self._client is not None:
                response = await self._post(
                    self._client,
                    headers,
                    payload,
                    timeout=request_timeout,
                )
            else:
                async with httpx.AsyncClient(timeout=request_timeout) as client:
                    response = await self._post(
                        client,
                        headers,
                        payload,
                        timeout=request_timeout,
                    )
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
            try:
                body = self._response_body(response)
                response_shape = self._response_shape(body)
                content, finish_reason, tool_calls = self._completion(body)
                usage = self._usage(body)
            except ProviderUnavailable as error:
                error.response_shape = locals().get("response_shape")  # type: ignore[attr-defined]
                raise
            except Exception as error:
                self._annotate_parser_failure(error, "unknown", "exception")
                error.response_shape = locals().get("response_shape")  # type: ignore[attr-defined]
                raise
            try:
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
            except Exception as error:
                self._annotate_parser_failure(error, "normalization", "exception")
                raise
            if observer is not None:
                observer.emit(
                    "provider_attempt_success",
                    attempt=attempt,
                    provider=self.provider.value,
                    outcome="success",
                    duration_ms=round((perf_counter() - started) * 1000, 2),
                    response_shape=response_shape,
                    **diagnostics,
                )
            return result
        except Exception as error:
            if observer is not None:
                failure_class = getattr(error, "safe_failure_category", None)
                if not isinstance(failure_class, str):
                    failure_class = normalized_failure_class(error)
                observer.emit(
                    "provider_attempt_failure",
                    attempt=attempt,
                    provider=self.provider.value,
                    outcome="failed",
                    failure_class=failure_class,
                    exception_type=type(error).__name__,
                    duration_ms=round((perf_counter() - started) * 1000, 2),
                    status_code=getattr(error, "status_code", None),
                    status_category=http_status_category(getattr(error, "status_code", None)),
                    parser_failure_stage=getattr(error, "parser_failure_stage", None),
                    parser_failure_detail=getattr(error, "parser_failure_detail", None),
                    response_shape=getattr(error, "response_shape", None),
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

    def _request_timeout(self, request: CompletionRequest) -> float:
        deadline = request.metadata.get("execution_deadline_monotonic")
        if not isinstance(deadline, (int, float)):
            return self._timeout_seconds

        remaining = deadline - asyncio.get_running_loop().time()
        budget = remaining - SAFETY_MARGIN_SECONDS
        if budget < MIN_TIMEOUT_SECONDS:
            logger.warning(
                "provider_timeout_budget_insufficient",
                extra={
                    "voice_session_id": (
                        request.metadata.get("voice_session_id")
                        if isinstance(request.metadata.get("voice_session_id"), str)
                        else None
                    ),
                    "deadline_remaining_ms": round(max(remaining, 0) * 1000, 2),
                },
            )
            raise ProviderTimeout("Groq request timed out")
        return min(budget, self._timeout_seconds)

    @staticmethod
    def _diagnostic_fields(request: CompletionRequest) -> dict[str, object]:
        return {
            key: request.metadata[key]
            for key in ("request_mode", "response_language")
            if isinstance(request.metadata.get(key), str)
        }

    @staticmethod
    def _parser_failure(
        message: str,
        stage: str,
        detail: str,
    ) -> ProviderUnavailable:
        return ProviderUnavailable(
            message,
            safe_failure_category="parser_error",
            parser_failure_stage=stage,
            parser_failure_detail=detail,
        )

    @staticmethod
    def _response_shape(body: Mapping[str, Any]) -> dict[str, object]:
        choices = body.get("choices")
        choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else None
        message = choice.get("message") if isinstance(choice, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        reasoning = message.get("reasoning") if isinstance(message, dict) else None
        tool_calls = message.get("tool_calls") if isinstance(message, dict) else None
        finish_reason = choice.get("finish_reason") if isinstance(choice, dict) else None
        if finish_reason is None:
            safe_finish_reason = "missing"
        elif isinstance(finish_reason, str) and finish_reason in {
            "stop", "length", "tool_calls", "function_call", "content_filter"
        }:
            safe_finish_reason = finish_reason
        else:
            safe_finish_reason = "unknown"
        return {
            "choices_count": len(choices) if isinstance(choices, list) else 0,
            "message_type": (
                "object" if isinstance(message, dict) else "missing" if message is None else "other"
            ),
            "content_type": (
                "missing" if not isinstance(message, dict) or "content" not in message
                else "null" if content is None
                else "string" if isinstance(content, str)
                else "other"
            ),
            "content_empty": content is None or (isinstance(content, str) and not content.strip()),
            "reasoning_present": isinstance(message, dict) and "reasoning" in message,
            "reasoning_type": (
                "missing" if not isinstance(message, dict) or "reasoning" not in message
                else "null" if reasoning is None
                else "string" if isinstance(reasoning, str)
                else "other"
            ),
            "tool_calls_present": isinstance(message, dict) and "tool_calls" in message,
            "tool_calls_count": len(tool_calls) if isinstance(tool_calls, list) else 0,
            "finish_reason": safe_finish_reason,
            "usage_present": "usage" in body,
        }

    @staticmethod
    def _annotate_parser_failure(
        error: BaseException,
        stage: str,
        detail: str,
    ) -> None:
        try:
            error.parser_failure_stage = stage  # type: ignore[attr-defined]
            error.parser_failure_detail = detail  # type: ignore[attr-defined]
        except Exception:
            pass

    async def _post(
        self,
        client: httpx.AsyncClient,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
        timeout: float | None = None,
    ) -> httpx.Response:
        try:
            response = await client.post(
                self.api_url,
                headers=headers,
                json=payload,
                timeout=timeout,
            )
        except httpx.TimeoutException as error:
            raise ProviderTimeout(
                "Groq request timed out",
                safe_failure_category="timeout",
            ) from error
        except httpx.HTTPError as error:
            raise ProviderUnavailable(
                "Groq request failed",
                safe_failure_category="transport_error",
            ) from error
        if response.status_code in {401, 403}:
            raise AuthenticationFailure(
                "Groq provider authentication failed",
                status_code=response.status_code,
                safe_failure_category=(
                    "unauthorized" if response.status_code == 401 else "forbidden"
                ),
            )
        if response.status_code == 408:
            raise ProviderTimeout(
                "Groq request timed out",
                status_code=response.status_code,
                safe_failure_category="timeout",
            )
        if response.status_code == 429:
            raise RateLimitExceeded(
                "Groq provider rate limit exceeded",
                status_code=response.status_code,
                safe_failure_category="rate_limited",
            )
        if response.status_code != 200:
            category = (
                "bad_request"
                if 400 <= response.status_code <= 499
                and response.status_code != 404
                else "not_found"
                if response.status_code == 404
                else "server_error"
                if 500 <= response.status_code <= 599
                else "provider_error"
            )
            raise ProviderUnavailable(
                "Groq provider rejected the request",
                status_code=response.status_code,
                safe_failure_category=category,
            )
        return response

    @staticmethod
    def _response_body(response: httpx.Response) -> dict[str, Any]:
        try:
            body = response.json()
        except ValueError as error:
            raise GroqProvider._parser_failure(
                "Groq provider returned invalid JSON", "json_decode", "exception"
            ) from error
        if not isinstance(body, dict):
            raise GroqProvider._parser_failure(
                "Groq provider returned an invalid response",
                "response_object",
                "wrong_type",
            )
        return body

    @classmethod
    def _completion(cls, body: Mapping[str, Any]) -> tuple[str, str, tuple[ProviderToolCall, ...]]:
        choices = body.get("choices")
        if "choices" not in body:
            raise cls._parser_failure(
                "Groq provider returned no usable output", "choices_missing", "missing_field"
            )
        if not isinstance(choices, list):
            raise cls._parser_failure(
                "Groq provider returned no usable output", "choices_missing", "wrong_type"
            )
        if not choices:
            raise cls._parser_failure(
                "Groq provider returned no usable output", "choices_empty", "empty_value"
            )
        if not isinstance(choices[0], dict):
            raise cls._parser_failure(
                "Groq provider returned no usable output",
                "choices_missing",
                "malformed_structure",
            )
        choice = choices[0]
        message = choice.get("message")
        if "message" not in choice:
            raise cls._parser_failure(
                "Groq provider returned no usable message", "message_invalid", "missing_field"
            )
        if not isinstance(message, dict):
            raise cls._parser_failure(
                "Groq provider returned no usable message", "message_invalid", "wrong_type"
            )
        content = message.get("content")
        if content is None:
            content = ""
        if not isinstance(content, str):
            raise cls._parser_failure(
                "Groq provider returned invalid content", "content_invalid", "wrong_type"
            )
        tool_calls = cls._tool_calls(message.get("tool_calls"))
        finish_reason = choice.get("finish_reason")
        if finish_reason not in cls._finish_reasons:
            raise cls._parser_failure(
                "Groq provider returned an invalid finish reason",
                "finish_reason_invalid",
                "unsupported_value",
            )
        if finish_reason == "tool_calls" and not tool_calls:
            raise cls._parser_failure(
                "Groq provider returned empty tool calls", "tool_calls_invalid", "empty_value"
            )
        if finish_reason in {"stop", "length"} and not content.strip():
            raise cls._parser_failure(
                "Groq provider returned empty output", "content_empty", "empty_value"
            )
        return content.strip(), finish_reason, tool_calls

    @staticmethod
    def _tool_calls(raw: object) -> tuple[ProviderToolCall, ...]:
        if raw is None:
            return ()
        if not isinstance(raw, list):
            raise GroqProvider._parser_failure(
                "Groq provider returned malformed tool calls", "tool_calls_invalid", "wrong_type"
            )
        result = []
        for item in raw:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                raise GroqProvider._parser_failure(
                    "Groq provider returned malformed tool call",
                    "tool_calls_invalid",
                    "malformed_structure",
                )
            function = item.get("function")
            if not isinstance(function, dict) or not isinstance(function.get("name"), str):
                raise GroqProvider._parser_failure(
                    "Groq provider returned malformed tool call",
                    "tool_calls_invalid",
                    "malformed_structure",
                )
            arguments = function.get("arguments", "{}")
            if not isinstance(arguments, str):
                raise GroqProvider._parser_failure(
                    "Groq provider returned malformed tool arguments",
                    "tool_calls_invalid",
                    "wrong_type",
                )
            try:
                decoded = json.loads(arguments)
            except (TypeError, ValueError) as error:
                raise GroqProvider._parser_failure(
                    "Groq provider returned invalid tool arguments",
                    "tool_calls_invalid",
                    "malformed_structure",
                ) from error
            if not isinstance(decoded, dict):
                raise GroqProvider._parser_failure(
                    "Groq provider tool arguments must be an object",
                    "tool_calls_invalid",
                    "wrong_type",
                )
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
            raise GroqProvider._parser_failure(
                "Groq provider returned invalid token usage", "usage_invalid", "wrong_type"
            )
        prompt_tokens = usage.get("prompt_tokens")
        completion_tokens = usage.get("completion_tokens")
        if (
            not isinstance(prompt_tokens, int) or isinstance(prompt_tokens, bool)
            or not isinstance(completion_tokens, int) or isinstance(completion_tokens, bool)
            or prompt_tokens < 0 or completion_tokens < 0
        ):
            raise GroqProvider._parser_failure(
                "Groq provider returned invalid token usage", "usage_invalid", "wrong_type"
            )
        return TokenUsage(prompt_tokens, completion_tokens)
