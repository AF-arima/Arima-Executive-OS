from __future__ import annotations

import asyncio
import inspect
import json
from time import perf_counter
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Protocol
from uuid import UUID

from pydantic import BaseModel, ValidationError

from app.providers.base import ProviderAdapter
from app.providers.types import (
    CompletionRequest,
    CompletionResponse,
    MessageRole,
    ProviderMessage,
    ProviderToolResult,
)


class NativeToolError(RuntimeError):
    """A model tool call was rejected before provider execution."""


class NativeAuthorizationError(NativeToolError):
    pass


class NativeToolRoundLimitError(NativeToolError):
    pass


class NativeToolAction(StrEnum):
    READ = "read"
    DRAFT = "draft"
    WRITE = "write"
    DESTRUCTIVE = "destructive"


@dataclass(frozen=True, slots=True)
class NativeExecutionContext:
    tenant_id: UUID
    workspace_id: UUID
    actor_id: UUID
    provider: str
    provider_account_id: str
    agent: str
    approved_call_ids: frozenset[str] = frozenset()
    destructive_guard_call_ids: frozenset[str] = frozenset()
    correlation_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class NativeToolProvenance:
    tenant_id: UUID
    workspace_id: UUID
    actor_id: UUID
    agent: str
    tool: str
    provider: str
    provider_account_id: str
    authorization: str
    timestamp: datetime
    status: str
    duration_ms: float


class NativeToolHandler(Protocol):
    def __call__(
        self, arguments: BaseModel, context: NativeExecutionContext
    ) -> Any | Awaitable[Any]: ...


@dataclass(frozen=True, slots=True)
class NativeToolSpec:
    canonical_name: str
    wire_name: str
    description: str
    arguments_model: type[BaseModel]
    action: NativeToolAction
    provider: str
    provider_account_required: bool
    handler: NativeToolHandler
    provenance_metadata: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if "." not in self.canonical_name:
            raise ValueError("Native tools require a canonical dotted name")
        if not self.wire_name or any(char in self.wire_name for char in ". "):
            raise ValueError("Native tool wire names must be NIM-safe")
        if self.provider_account_required and not self.provider:
            raise ValueError("Provider-account tools require a provider")

    @property
    def schema(self) -> dict[str, Any]:
        return self.arguments_model.model_json_schema()

    def declaration(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.wire_name,
                "description": self.description,
                "parameters": self.schema,
            },
        }

    def validate(self, arguments: dict[str, Any]) -> BaseModel:
        unknown = set(arguments) - set(self.arguments_model.model_fields)
        if unknown:
            raise NativeToolError(
                f"Invalid arguments for tool {self.canonical_name}"
            )
        try:
            return self.arguments_model.model_validate(arguments)
        except (ValidationError, TypeError) as error:
            raise NativeToolError(
                f"Invalid arguments for tool {self.canonical_name}"
            ) from error


class NativeToolRegistry:
    def __init__(self, tools: tuple[NativeToolSpec, ...] = ()) -> None:
        self._by_wire: dict[str, NativeToolSpec] = {}
        self._by_canonical: dict[str, NativeToolSpec] = {}
        for tool in tools:
            self.register(tool)

    def register(self, tool: NativeToolSpec) -> None:
        if tool.wire_name in self._by_wire or tool.canonical_name in self._by_canonical:
            raise ValueError("Duplicate native tool name")
        self._by_wire[tool.wire_name] = tool
        self._by_canonical[tool.canonical_name] = tool

    def resolve_wire(self, wire_name: str) -> NativeToolSpec:
        try:
            return self._by_wire[wire_name]
        except KeyError as error:
            raise NativeToolError(f"Unknown native tool: {wire_name}") from error

    def declarations(self) -> tuple[dict[str, Any], ...]:
        return tuple(tool.declaration() for tool in self._by_wire.values())


@dataclass(frozen=True, slots=True)
class NativeToolExecution:
    canonical_name: str
    call_id: str
    provenance: NativeToolProvenance
    result: Any


class NativeToolLoop:
    _MIN_CONTINUATION_OUTPUT_TOKENS = 1_024

    def __init__(
        self,
        provider: ProviderAdapter,
        registry: NativeToolRegistry,
        *,
        max_rounds: int = 4,
        timeout_seconds: float = 60.0,
    ) -> None:
        if max_rounds < 1:
            raise ValueError("Native tool round limit must be positive")
        self.provider = provider
        self.registry = registry
        self.max_rounds = max_rounds
        self.timeout_seconds = timeout_seconds

    async def complete(
        self,
        *,
        model: str,
        messages: tuple[ProviderMessage, ...],
        max_output_tokens: int,
        context: NativeExecutionContext,
        cancel_check: Callable[[], bool] | None = None,
        stale_check: Callable[[], bool] | None = None,
    ) -> tuple[CompletionResponse, tuple[NativeToolExecution, ...]]:
        conversation = list(messages)
        executions: list[NativeToolExecution] = []
        continuation_budget = self._continuation_budget(
            model, max_output_tokens
        )
        for _round in range(self.max_rounds):
            self._check_state(cancel_check, stale_check)
            response = await asyncio.wait_for(
                self.provider.complete(
                    CompletionRequest(
                        model=model,
                        messages=tuple(conversation),
                        max_output_tokens=continuation_budget,
                        tools=self.registry.declarations(),
                        metadata={
                            "tool_choice": "auto",
                            "chat_template_kwargs": {
                                "enable_thinking": False
                            },
                        },
                    )
                ),
                timeout=self.timeout_seconds,
            )
            if not response.tool_calls:
                return response, tuple(executions)
            conversation.append(
                ProviderMessage(
                    role=MessageRole.ASSISTANT,
                    content=response.content,
                    tool_calls=response.tool_calls,
                )
            )
            for call in response.tool_calls:
                self._check_state(cancel_check, stale_check)
                tool = self.registry.resolve_wire(call.wire_name)
                arguments = tool.validate(call.arguments)
                try:
                    started = perf_counter()
                    self._authorize(tool, context, call.call_id)
                    result = tool.handler(arguments, context)
                    if inspect.isawaitable(result):
                        result = await asyncio.wait_for(
                            result, timeout=self.timeout_seconds
                        )
                    status = "succeeded"
                    authorization = "allowed"
                except NativeAuthorizationError as error:
                    result = {"error": str(error)}
                    status = "authorization_denied"
                    authorization = "denied"
                except Exception as error:
                    result = {"error": f"Tool execution failed ({type(error).__name__})"}
                    status = "provider_error"
                    authorization = "allowed"
                duration_ms = max(round((perf_counter() - started) * 1000, 3), 0)
                provenance = NativeToolProvenance(
                    tenant_id=context.tenant_id,
                    workspace_id=context.workspace_id,
                    actor_id=context.actor_id,
                    agent=context.agent,
                    tool=tool.canonical_name,
                    provider=tool.provider,
                    provider_account_id=context.provider_account_id,
                    authorization=authorization,
                    timestamp=datetime.now(timezone.utc),
                    status=status,
                    duration_ms=duration_ms,
                )
                executions.append(
                    NativeToolExecution(
                        canonical_name=tool.canonical_name,
                        call_id=call.call_id,
                        provenance=provenance,
                        result=result,
                    )
                )
                conversation.append(
                    ProviderMessage(
                        role=MessageRole.TOOL,
                        tool_result=ProviderToolResult(
                            call_id=call.call_id,
                            wire_name=call.wire_name,
                            serialized_result=_safe_serialize(result),
                        ),
                    )
                )
        raise NativeToolRoundLimitError("Maximum native tool rounds exceeded")

    def _continuation_budget(self, model: str, requested: int) -> int:
        provider_limit = self.provider.model_information(model).max_output_tokens
        return min(
            provider_limit,
            max(requested, self._MIN_CONTINUATION_OUTPUT_TOKENS),
        )

    @staticmethod
    def _authorize(
        tool: NativeToolSpec, context: NativeExecutionContext, call_id: str
    ) -> None:
        if tool.provider_account_required and not context.provider_account_id:
            raise NativeAuthorizationError("Provider account is required")
        if tool.provider != context.provider:
            raise NativeAuthorizationError("Provider account mismatch")
        if tool.action in {NativeToolAction.WRITE, NativeToolAction.DESTRUCTIVE}:
            if call_id not in context.approved_call_ids:
                raise NativeAuthorizationError("Explicit confirmation is required")
        if tool.action is NativeToolAction.DESTRUCTIVE:
            if call_id not in context.destructive_guard_call_ids:
                raise NativeAuthorizationError("Destructive safety guard is required")

    @staticmethod
    def _check_state(
        cancel_check: Callable[[], bool] | None,
        stale_check: Callable[[], bool] | None,
    ) -> None:
        if cancel_check is not None and cancel_check():
            raise asyncio.CancelledError
        if stale_check is not None and stale_check():
            raise NativeToolError("Stale orchestration turn")


def _safe_serialize(value: Any) -> str:
    def clean(item: Any) -> Any:
        if isinstance(item, Mapping):
            return {
                str(key): "[REDACTED]"
                if any(token in str(key).lower() for token in ("token", "secret", "password", "api_key"))
                else clean(subvalue)
                for key, subvalue in item.items()
            }
        if isinstance(item, (list, tuple)):
            return [clean(subvalue) for subvalue in item]
        return item

    try:
        return json.dumps(clean(value), default=str, separators=(",", ":"))
    except (TypeError, ValueError):
        return json.dumps({"error": "Tool returned an unserializable result"})
