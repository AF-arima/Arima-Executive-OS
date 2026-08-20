import asyncio
from uuid import uuid4

import httpx
import pytest
from datetime import datetime, timezone

from app.integrations.microsoft_graph import (
    MicrosoftGraphClient,
    MicrosoftGraphResponseError,
    MicrosoftPermissionError,
    MicrosoftProviderUnavailableError,
    MicrosoftResourceNotFoundError,
    _message,
    _messages,
    _utc_datetime,
    build_native_registry,
)
from app.orchestration.native_tools import NativeExecutionContext


def execution_context() -> NativeExecutionContext:
    return NativeExecutionContext(
        tenant_id=uuid4(),
        workspace_id=uuid4(),
        actor_id=uuid4(),
        provider="microsoft",
        provider_account_id="ms-account-1",
        agent="executive",
    )


def test_sqlite_style_token_expiry_is_normalized_to_utc():
    value = _utc_datetime(datetime(2026, 8, 20, 12, 0, 0))
    assert value.tzinfo is timezone.utc


class Resolver:
    def __init__(self):
        self.refreshes = 0

    async def resolve(self, context, *, force_refresh=False):
        if force_refresh:
            self.refreshes += 1
        return object(), "server-token", "server-refresh"


def test_graph_results_are_normalized_and_limited():
    result = _messages(
        {"value": [{
            "id": "message-1",
            "conversationId": "thread-1",
            "sender": {"emailAddress": {"address": "sender@example.com"}},
            "subject": "Subject",
            "receivedDateTime": "2026-08-20T10:00:00Z",
            "isRead": False,
            "bodyPreview": "Preview",
            "hasAttachments": True,
            "internetMessageHeaders": [{"name": "secret", "value": "omit"}],
        }, {"id": "message-2"}]},
        1,
    )
    assert result == {
        "items": [{
            "id": "message-1",
            "conversation_id": "thread-1",
            "sender": "sender@example.com",
            "to": [],
            "subject": "Subject",
            "received_at": "2026-08-20T10:00:00Z",
            "is_unread": True,
            "preview": "Preview",
            "has_attachments": True,
        }],
        "count": 1,
        "next_page": None,
    }


@pytest.mark.asyncio
async def test_graph_client_refreshes_once_on_401_without_exposing_tokens():
    resolver = Resolver()
    statuses = [401, 200]

    async def handler(request: httpx.Request):
        assert request.headers["Authorization"] == "Bearer server-token"
        return httpx.Response(statuses.pop(0), json={"value": []})

    client = MicrosoftGraphClient(
        resolver,
        execution_context(),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    result = await client.request("GET", "/me/messages")
    assert result == {"value": []}
    assert resolver.refreshes == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,expected",
    [
        (403, MicrosoftPermissionError),
        (404, MicrosoftResourceNotFoundError),
        (500, MicrosoftProviderUnavailableError),
    ],
)
async def test_graph_client_maps_provider_failures(status, expected):
    async def handler(request: httpx.Request):
        return httpx.Response(status, json={"error": "private provider detail"})

    client = MicrosoftGraphClient(
        Resolver(),
        execution_context(),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(expected):
        await client.request("GET", "/me/messages")


@pytest.mark.asyncio
async def test_graph_client_rejects_malformed_json():
    async def handler(request: httpx.Request):
        return httpx.Response(200, content=b"not-json")

    client = MicrosoftGraphClient(
        Resolver(),
        execution_context(),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(MicrosoftGraphResponseError):
        await client.request("GET", "/me/messages")


def test_microsoft_registry_has_safe_canonical_and_wire_names():
    registry = build_native_registry(object())
    declarations = registry.declarations()
    assert len(declarations) == 13
    assert registry.resolve_wire("email_list_recent").canonical_name == "email.list_recent"
    assert all("." not in item["function"]["name"] for item in declarations)
    assert all(item["function"]["parameters"]["type"] == "object" for item in declarations)
