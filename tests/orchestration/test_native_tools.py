import asyncio
import json
from uuid import uuid4

import pytest
from pydantic import BaseModel, ConfigDict

from app.orchestration.native_tools import (
    NativeAuthorizationError,
    NativeExecutionContext,
    NativeToolAction,
    NativeToolError,
    NativeToolLoop,
    NativeToolRegistry,
    NativeToolRoundLimitError,
    NativeToolSpec,
)
from app.providers.base import ConfiguredProviderStub
from app.providers.types import (
    CompletionResponse,
    EstimatedCost,
    MessageRole,
    ModelInfo,
    ProviderCapabilities,
    ProviderMessage,
    ProviderName,
    ProviderToolCall,
    ProviderStatus,
    ProviderHealth,
    StreamChunk,
    TokenUsage,
)


class Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str


class FakeProvider(ConfiguredProviderStub):
    def __init__(self, responses: list[CompletionResponse]):
        super().__init__(
            ModelInfo(
                provider=ProviderName.NVIDIA,
                model="test-model",
                display_name="test",
                context_window=4096,
                max_output_tokens=512,
                capabilities=ProviderCapabilities(tools=True),
            )
        )
        self.responses = responses
        self.requests = []

    async def complete(self, request):
        self.requests.append(request)
        return self.responses.pop(0)


def response(*, content="", tool_calls=(), finish_reason="stop"):
    return CompletionResponse(
        provider=ProviderName.NVIDIA,
        model="test-model",
        content=content,
        usage=TokenUsage(1, 1),
        estimated_cost=EstimatedCost(0, 0, 0),
        finish_reason=finish_reason,
        tool_calls=tool_calls,
    )


def context(**kwargs):
    return NativeExecutionContext(
        tenant_id=uuid4(),
        workspace_id=uuid4(),
        actor_id=uuid4(),
        provider="microsoft",
        provider_account_id="account-1",
        agent="executive",
        **kwargs,
    )


def tool(handler, *, action=NativeToolAction.READ):
    return NativeToolSpec(
        canonical_name="email.search",
        wire_name="email_search",
        description="Search email",
        arguments_model=Arguments,
        action=action,
        provider="microsoft",
        provider_account_required=True,
        handler=handler,
    )


@pytest.mark.asyncio
async def test_native_loop_executes_tool_and_sends_safe_result_to_follow_up():
    async def handler(arguments, execution_context):
        return {"matches": [arguments.query], "access_token": "never"}

    provider = FakeProvider([
        response(
            tool_calls=(ProviderToolCall("email_search", "call-1", {"query": "ARIMA"}),),
            finish_reason="tool_calls",
        ),
        response(content="I found one matching message."),
    ])
    registry = NativeToolRegistry((tool(handler),))
    final, executions = await NativeToolLoop(provider, registry).complete(
        model="test-model",
        messages=(ProviderMessage(role=MessageRole.USER, content="Search"),),
        max_output_tokens=100,
        context=context(),
    )

    assert final.content == "I found one matching message."
    assert len(executions) == 1
    assert "never" not in provider.requests[1].messages[-1].tool_result.serialized_result
    assert provider.requests[1].messages[-1].tool_result.call_id == "call-1"
    assert provider.requests[0].tools[0]["function"]["name"] == "email_search"
    assert provider.requests[0].max_output_tokens == 512
    assert provider.requests[1].max_output_tokens == 512


@pytest.mark.asyncio
async def test_native_loop_continuation_budget_prevents_truncated_final_response():
    async def handler(arguments, execution_context):
        return {"items": [], "count": 0}

    provider = FakeProvider([
        response(
            tool_calls=(ProviderToolCall("email_search", "call-1", {"query": "ARIMA"}),),
            finish_reason="tool_calls",
        ),
        response(content="The read completed without truncation."),
    ])
    final, _ = await NativeToolLoop(provider, NativeToolRegistry((tool(handler),))).complete(
        model="test-model",
        messages=(ProviderMessage(role=MessageRole.USER, content="Search"),),
        max_output_tokens=256,
        context=context(),
    )

    assert final.content == "The read completed without truncation."
    assert provider.requests[0].max_output_tokens == 512
    assert provider.requests[1].max_output_tokens == 512


@pytest.mark.asyncio
async def test_native_loop_rejects_unknown_and_invalid_tools():
    provider = FakeProvider([
        response(
            tool_calls=(ProviderToolCall("unknown", "call-1", {}),),
            finish_reason="tool_calls",
        )
    ])
    with pytest.raises(NativeToolError, match="Unknown native tool"):
        await NativeToolLoop(provider, NativeToolRegistry()).complete(
            model="test-model",
            messages=(ProviderMessage(role=MessageRole.USER, content="Run"),),
            max_output_tokens=100,
            context=context(),
        )

    provider = FakeProvider([
        response(
            tool_calls=(ProviderToolCall("email_search", "call-1", {"extra": 1}),),
            finish_reason="tool_calls",
        )
    ])
    with pytest.raises(NativeToolError, match="Invalid arguments"):
        await NativeToolLoop(provider, NativeToolRegistry((tool(lambda *_: {}),))).complete(
            model="test-model",
            messages=(ProviderMessage(role=MessageRole.USER, content="Run"),),
            max_output_tokens=100,
            context=context(),
        )


@pytest.mark.asyncio
async def test_native_loop_requires_exact_confirmation_for_write():
    called = False

    async def handler(arguments, execution_context):
        nonlocal called
        called = True
        return {"ok": True}

    provider = FakeProvider([
        response(
            tool_calls=(ProviderToolCall("email_search", "call-1", {"query": "x"}),),
            finish_reason="tool_calls",
        ),
        response(content="Confirmation is required."),
    ])
    final, _ = await NativeToolLoop(
        provider, NativeToolRegistry((tool(handler, action=NativeToolAction.WRITE),))
    ).complete(
        model="test-model",
        messages=(ProviderMessage(role=MessageRole.USER, content="Send"),),
        max_output_tokens=100,
        context=context(),
    )
    assert not called
    assert final.content == "Confirmation is required."


@pytest.mark.asyncio
async def test_native_loop_honors_cancellation_and_round_limit():
    provider = FakeProvider([
        response(
            tool_calls=(ProviderToolCall("email_search", "call-1", {"query": "x"}),),
            finish_reason="tool_calls",
        )
    ])
    with pytest.raises(asyncio.CancelledError):
        await NativeToolLoop(provider, NativeToolRegistry((tool(lambda *_: {}),))).complete(
            model="test-model",
            messages=(ProviderMessage(role=MessageRole.USER, content="Run"),),
            max_output_tokens=100,
            context=context(),
            cancel_check=lambda: True,
        )

    provider = FakeProvider([
        response(
            tool_calls=(ProviderToolCall("email_search", "call-1", {"query": "x"}),),
            finish_reason="tool_calls",
        )
    ])
    with pytest.raises(NativeToolRoundLimitError):
        await NativeToolLoop(
            provider, NativeToolRegistry((tool(lambda *_: {}),)), max_rounds=1
        ).complete(
            model="test-model",
            messages=(ProviderMessage(role=MessageRole.USER, content="Run"),),
            max_output_tokens=100,
            context=context(),
        )
