from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
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
    ModelInfo,
    ProviderHealth,
    ProviderName,
    ProviderStatus,
    StreamChunk,
    TokenUsage,
    ProviderToolCall,
    ProviderToolResult,
)
from app.voice.observability import (
    http_status_category,
    normalized_failure_class,
    observer_from_metadata,
)

logger = logging.getLogger("arima.provider.execution")


class NvidiaProvider(ProviderAdapter):
    """Server-side NVIDIA NIM chat-completions adapter."""

    api_url = "https://integrate.api.nvidia.com/v1/chat/completions"
    user_agent = "arima-executive-os/0.1"

    def __init__(
        self,
        config: ProviderConfig,
        *,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 60.0,
    ) -> None:
        if config.provider is not ProviderName.NVIDIA:
            raise ProviderConfigurationError(
                "NvidiaProvider requires nvidia configuration"
            )
        self.config = config
        self._client = client
        self._timeout_seconds = timeout_seconds
        self._model_info = ModelInfo(
            provider=config.provider,
            model=config.default_model,
            display_name="NVIDIA NIM API",
            context_window=config.max_model_tokens,
            max_output_tokens=config.max_output_tokens,
            capabilities=config.capabilities,
        )

    @property
    def provider(self) -> ProviderName:
        return ProviderName.NVIDIA

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
                "NVIDIA provider is configured."
                if configured
                else "NVIDIA provider credentials are unavailable."
            ),
        )

    async def complete(
        self,
        request: CompletionRequest,
    ) -> CompletionResponse:
        self._validate_request(request)
        headers = {
            "Authorization": f"Bearer {self._api_key()}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": self.user_agent,
        }
        payload: dict[str, object] = {
            "model": request.model,
            "messages": [self._message_payload(message) for message in request.messages],
            "temperature": request.temperature,
            "max_tokens": min(
                request.max_output_tokens,
                self.config.max_output_tokens,
            ),
            "stream": False,
        }
        if request.tools:
            payload["tools"] = list(request.tools)
            payload["tool_choice"] = request.metadata.get("tool_choice", "auto")
            chat_template_kwargs = request.metadata.get(
                "chat_template_kwargs"
            )
            if isinstance(chat_template_kwargs, Mapping):
                payload["chat_template_kwargs"] = dict(chat_template_kwargs)
        diagnostics = self._diagnostic_fields(request)
        observer = observer_from_metadata(request.metadata)
        raw_attempt = request.metadata.get("provider_attempt")
        attempt = raw_attempt if isinstance(raw_attempt, int) else None
        started = perf_counter()
        trace = request.metadata.get("_boundary_trace")
        if isinstance(trace, list):
            trace.append("E_PROVIDER_ENTRY")
        if observer is not None:
            observer.emit(
                "provider_attempt_start",
                attempt=attempt,
                provider=self.provider.value,
                outcome="started",
                provider_timeout_ms=self._timeout_seconds * 1000,
                request_mode=diagnostics.get("request_mode"),
                response_language=diagnostics.get("response_language"),
                model=request.model,
            )
        logger.info(
            "provider_call_started",
            extra={
                **diagnostics,
                "provider": self.provider.value,
                "model": request.model,
                "provider_timeout_ms": round(self._timeout_seconds * 1000, 2),
            },
        )
        try:
            if observer is not None:
                observer.emit(
                    "provider_request_dispatched",
                    attempt=attempt,
                    provider=self.provider.value,
                    outcome="dispatched",
                    request_mode=diagnostics.get("request_mode"),
                    response_language=diagnostics.get("response_language"),
                    model=request.model,
                )
            if self._client is not None:
                response = await self._post(
                    self._client,
                    headers,
                    payload,
                    on_response=(
                        lambda raw: observer.emit(
                            "provider_response_received",
                            attempt=attempt,
                            provider=self.provider.value,
                            outcome="received",
                            status_code=raw.status_code,
                            status_category=http_status_category(raw.status_code),
                            duration_ms=round((perf_counter() - started) * 1000, 2),
                            request_mode=diagnostics.get("request_mode"),
                            response_language=diagnostics.get("response_language"),
                            model=request.model,
                        )
                    ) if observer is not None else None,
                )
            else:
                async with httpx.AsyncClient(
                    timeout=self._timeout_seconds
                ) as client:
                    response = await self._post(
                        client,
                        headers,
                        payload,
                        on_response=(
                            lambda raw: observer.emit(
                                "provider_response_received",
                                attempt=attempt,
                                provider=self.provider.value,
                                outcome="received",
                                status_code=raw.status_code,
                                status_category=http_status_category(raw.status_code),
                                duration_ms=round((perf_counter() - started) * 1000, 2),
                                request_mode=diagnostics.get("request_mode"),
                                response_language=diagnostics.get("response_language"),
                                model=request.model,
                            )
                        ) if observer is not None else None,
                    )
            if isinstance(trace, list):
                trace.append("F_PROVIDER_RETURN")
            body = self._response_body(response)
            content, finish_reason, tool_calls = self._completion(body)
            usage = self._usage(body)
        except Exception as error:
            if observer is not None:
                failure_class = normalized_failure_class(error)
                observer.emit(
                    "provider_attempt_failure",
                    attempt=attempt,
                    provider=self.provider.value,
                    outcome="failed",
                    duration_ms=round((perf_counter() - started) * 1000, 2),
                    failure_class=failure_class,
                    timeout_category=(failure_class if "timeout" in failure_class else None),
                    exception_type=type(error).__name__,
                    status_code=getattr(error, "status_code", None),
                    status_category=http_status_category(getattr(error, "status_code", None)),
                    request_mode=diagnostics.get("request_mode"),
                    response_language=diagnostics.get("response_language"),
                    model=request.model,
                )
            logger.warning(
                "provider_call_failed",
                extra={
                    **diagnostics,
                    "provider": self.provider.value,
                    "model": request.model,
                    "duration_ms": round((perf_counter() - started) * 1000, 2),
                    "failure_class": normalized_failure_class(error),
                    "exception_type": type(error).__name__,
                    "status_code": getattr(error, "status_code", None),
                },
            )
            raise
        if observer is not None:
            observer.emit(
                "provider_attempt_success",
                attempt=attempt,
                provider=self.provider.value,
                outcome="success",
                duration_ms=round((perf_counter() - started) * 1000, 2),
                request_mode=diagnostics.get("request_mode"),
                response_language=diagnostics.get("response_language"),
                model=request.model,
            )
        logger.info(
            "provider_call_finished",
            extra={
                **diagnostics,
                "provider": self.provider.value,
                "model": request.model,
                "duration_ms": round((perf_counter() - started) * 1000, 2),
                "status_code": response.status_code,
            },
        )
        latency_ms = max(int((perf_counter() - started) * 1_000), 0)
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
            finish_reason=finish_reason,
            tool_calls=tool_calls,
            metadata={
                "response_id": (
                    response_id if isinstance(response_id, str) else None
                ),
                "latency_ms": latency_ms,
            },
        )

    async def stream(
        self,
        request: CompletionRequest,
    ) -> AsyncIterator[StreamChunk]:
        response = await self.complete(request)
        if response.tool_calls:
            for index, call in enumerate(response.tool_calls):
                yield StreamChunk(
                    provider=self.provider,
                    model=response.model,
                    index=index,
                    content="",
                    tool_call=call,
                    finished=index == len(response.tool_calls) - 1,
                )
            return
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
            "NVIDIA embeddings are not enabled for this adapter"
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
                "NVIDIA provider credentials are unavailable"
            )
        return value

    def _validate_request(self, request: CompletionRequest) -> None:
        self.require_model(request.model)
        if request.json_mode:
            raise ProviderConfigurationError(
                "NVIDIA provider JSON mode is not enabled"
            )
        if any(message.images for message in request.messages):
            raise ProviderConfigurationError(
                "NVIDIA provider image input is not enabled"
            )
        if request.temperature > 1:
            raise ProviderConfigurationError(
                "NVIDIA provider temperature must not exceed one"
            )

    @staticmethod
    def _diagnostic_fields(request: CompletionRequest) -> dict[str, object]:
        allowed = {
            "trace_id": request.metadata.get("voice_trace_id"),
            "voice_session_id": request.metadata.get("voice_session_id"),
            "request_mode": request.metadata.get("request_mode"),
            "response_language": request.metadata.get("response_language"),
        }
        return {
            key: value
            for key, value in allowed.items()
            if isinstance(value, str)
        }

    @staticmethod
    def _with_status(error: Exception, status_code: int) -> Exception:
        setattr(error, "status_code", status_code)
        return error

    async def _post(
        self,
        client: httpx.AsyncClient,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
        on_response: Any = None,
    ) -> httpx.Response:
        try:
            response = await client.post(
                self.api_url,
                headers=headers,
                json=payload,
            )
        except httpx.TimeoutException as error:
            raise ProviderTimeout("NVIDIA request timed out") from error
        except httpx.HTTPError as error:
            raise ProviderUnavailable("NVIDIA request failed") from error
        if on_response is not None:
            on_response(response)
        if response.status_code in {401, 403}:
            raise self._with_status(
                AuthenticationFailure("NVIDIA provider authentication failed"),
                response.status_code,
            )
        if response.status_code == 429:
            raise self._with_status(
                RateLimitExceeded("NVIDIA provider rate limit exceeded"),
                response.status_code,
            )
        if response.status_code != 200:
            raise self._with_status(
                ProviderUnavailable(
                    "NVIDIA provider rejected the request"
                ),
                response.status_code,
            )
        return response

    @staticmethod
    def _response_body(response: httpx.Response) -> dict[str, Any]:
        try:
            body = response.json()
        except ValueError as error:
            raise ProviderUnavailable(
                "NVIDIA provider returned an invalid response"
            ) from error
        if not isinstance(body, dict):
            raise ProviderUnavailable(
                "NVIDIA provider returned an invalid response"
            )
        return body

    @staticmethod
    def _completion(body: Mapping[str, Any]) -> tuple[str, str, tuple[ProviderToolCall, ...]]:
        choices = body.get("choices")
        if not isinstance(choices, list) or len(choices) != 1:
            raise ProviderUnavailable(
                "NVIDIA provider returned no usable output"
            )
        choice = choices[0]
        if not isinstance(choice, dict):
            raise ProviderUnavailable(
                "NVIDIA provider returned no usable output"
            )
        message = choice.get("message")
        if not isinstance(message, dict):
            raise ProviderUnavailable(
                "NVIDIA provider returned no usable output"
            )
        content = message.get("content") or ""
        if not isinstance(content, str):
            raise ProviderUnavailable("NVIDIA provider returned invalid content")
        tool_calls = NvidiaProvider._parse_tool_calls(message.get("tool_calls"))
        finish_reason = choice.get("finish_reason")
        if finish_reason not in {"stop", "tool_calls"}:
            detail = (
                f"finish_reason={finish_reason}"
                if isinstance(finish_reason, str)
                else "finish_reason=missing_or_invalid"
            )
            raise ProviderUnavailable(
                f"NVIDIA provider returned an incomplete response ({detail})"
            )
        if finish_reason == "stop" and not content.strip():
            raise ProviderUnavailable("NVIDIA provider returned no usable output")
        if finish_reason == "tool_calls" and not tool_calls:
            raise ProviderUnavailable("NVIDIA provider returned empty tool calls")
        return content.strip(), finish_reason, tool_calls

    @staticmethod
    def _parse_tool_calls(raw: object) -> tuple[ProviderToolCall, ...]:
        if raw is None:
            return ()
        if not isinstance(raw, list):
            raise ProviderUnavailable("NVIDIA provider returned malformed tool calls")
        parsed: list[ProviderToolCall] = []
        for item in raw:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                raise ProviderUnavailable("NVIDIA provider returned malformed tool call")
            function = item.get("function")
            if not isinstance(function, dict) or not isinstance(function.get("name"), str):
                raise ProviderUnavailable("NVIDIA provider returned malformed tool call")
            arguments = function.get("arguments", "{}")
            if not isinstance(arguments, str):
                raise ProviderUnavailable("NVIDIA provider returned malformed tool arguments")
            try:
                decoded = json.loads(arguments)
            except (TypeError, ValueError) as error:
                raise ProviderUnavailable("NVIDIA provider returned invalid tool arguments") from error
            if not isinstance(decoded, dict):
                raise ProviderUnavailable("NVIDIA provider tool arguments must be an object")
            parsed.append(ProviderToolCall(function["name"], item["id"], decoded))
        return tuple(parsed)

    @staticmethod
    def _message_payload(message: Any) -> dict[str, Any]:
        if message.role.value == "tool":
            result: ProviderToolResult = message.tool_result
            return {
                "role": "tool",
                "tool_call_id": result.call_id,
                "name": result.wire_name,
                "content": result.serialized_result,
            }
        payload: dict[str, Any] = {"role": message.role.value, "content": message.content}
        if message.tool_calls:
            payload["tool_calls"] = [
                {"id": call.call_id, "type": "function", "function": {
                    "name": call.wire_name, "arguments": json.dumps(call.arguments, separators=(",", ":")),
                }}
                for call in message.tool_calls
            ]
        return payload

    @staticmethod
    def _usage(body: Mapping[str, Any]) -> TokenUsage:
        usage = body.get("usage")
        if not isinstance(usage, dict):
            raise ProviderUnavailable(
                "NVIDIA provider returned invalid token usage"
            )
        input_tokens = usage.get("prompt_tokens")
        output_tokens = usage.get("completion_tokens")
        if (
            not isinstance(input_tokens, int)
            or isinstance(input_tokens, bool)
            or not isinstance(output_tokens, int)
            or isinstance(output_tokens, bool)
            or input_tokens < 0
            or output_tokens < 0
        ):
            raise ProviderUnavailable(
                "NVIDIA provider returned invalid token usage"
            )
        return TokenUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
