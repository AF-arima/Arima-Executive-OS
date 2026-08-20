from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.database.models import OAuthCredential
from app.integrations.microsoft import PROVIDER
from app.integrations.secret_box import decrypt_json, encrypt_json
from app.orchestration.native_tools import (
    NativeExecutionContext,
    NativeToolAction,
    NativeToolRegistry,
    NativeToolSpec,
)

GRAPH_ROOT = "https://graph.microsoft.com/v1.0"
MAX_PAGE_SIZE = 50
TOKEN_REFRESH_WINDOW = timedelta(minutes=2)


def _utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class MicrosoftGraphError(RuntimeError):
    pass


class MicrosoftNotConfiguredError(MicrosoftGraphError):
    pass


class MicrosoftAmbiguousAccountError(MicrosoftGraphError):
    pass


class MicrosoftAuthenticationError(MicrosoftGraphError):
    pass


class MicrosoftPermissionError(MicrosoftGraphError):
    pass


class MicrosoftResourceNotFoundError(MicrosoftGraphError):
    pass


class MicrosoftProviderUnavailableError(MicrosoftGraphError):
    pass


class MicrosoftGraphResponseError(MicrosoftGraphError):
    pass


class _LimitArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    limit: int = Field(default=10, ge=1, le=MAX_PAGE_SIZE)


class _SearchArgs(_LimitArgs):
    query: str = Field(min_length=1, max_length=200)


class _MessageArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message_id: str = Field(min_length=1, max_length=500)


class _DraftReplyArgs(_MessageArgs):
    body: str = Field(min_length=1, max_length=20_000)


class _NewDraftArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    to: tuple[str, ...] = Field(min_length=1, max_length=50)
    subject: str = Field(min_length=1, max_length=500)
    body: str = Field(min_length=1, max_length=20_000)
    cc: tuple[str, ...] = Field(default=(), max_length=50)
    bcc: tuple[str, ...] = Field(default=(), max_length=50)


class _ProposedResponseArgs(_MessageArgs):
    response: str = Field(min_length=1, max_length=20_000)


def _credential_query(
    tenant_id: UUID, workspace_id: UUID, actor_id: UUID, account_id: str | None = None
):
    query = select(OAuthCredential).where(
        OAuthCredential.tenant_id == tenant_id,
        OAuthCredential.workspace_id == workspace_id,
        OAuthCredential.actor_id == actor_id,
        OAuthCredential.provider == PROVIDER,
        OAuthCredential.revoked_at.is_(None),
    )
    if account_id is not None:
        query = query.where(OAuthCredential.provider_account_id == account_id)
    return query


class MicrosoftCredentialResolver:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def resolve(
        self, context: NativeExecutionContext, *, force_refresh: bool = False
    ) -> tuple[OAuthCredential, str, str]:
        if not get_settings().microsoft_integration_enabled:
            raise MicrosoftNotConfiguredError("Microsoft integration is disabled")
        rows = (
            await self.session.scalars(
                _credential_query(
                    context.tenant_id,
                    context.workspace_id,
                    context.actor_id,
                    context.provider_account_id,
                )
            )
        ).all()
        if not rows:
            raise MicrosoftNotConfiguredError("Microsoft account is not configured")
        if len(rows) != 1:
            raise MicrosoftAmbiguousAccountError("Microsoft account identity is ambiguous")
        credential = rows[0]
        access = decrypt_json(
            credential.encrypted_access_token, purpose="microsoft-access-token"
        ).get("value")
        refresh = decrypt_json(
            credential.encrypted_refresh_token, purpose="microsoft-refresh-token"
        ).get("value")
        if not isinstance(access, str) or not isinstance(refresh, str):
            raise MicrosoftAuthenticationError("Microsoft credential is invalid")
        if force_refresh or (
            credential.token_expires_at
            and _utc_datetime(credential.token_expires_at)
            <= datetime.now(UTC) + TOKEN_REFRESH_WINDOW
        ):
            access, refresh = await self._refresh(credential, refresh)
        return credential, access, refresh

    async def account_id(
        self, *, tenant_id: UUID, workspace_id: UUID, actor_id: UUID
    ) -> str:
        if not get_settings().microsoft_integration_enabled:
            raise MicrosoftNotConfiguredError("Microsoft integration is disabled")
        rows = (
            await self.session.scalars(
                _credential_query(tenant_id, workspace_id, actor_id)
            )
        ).all()
        if not rows:
            raise MicrosoftNotConfiguredError("Microsoft account is not configured")
        if len(rows) != 1:
            raise MicrosoftAmbiguousAccountError("Microsoft account identity is ambiguous")
        return rows[0].provider_account_id

    async def _refresh(
        self, credential: OAuthCredential, refresh_token: str
    ) -> tuple[str, str]:
        settings = get_settings()
        endpoint = f"{settings.microsoft_authority.rstrip('/')}/oauth2/v2.0/token"
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    endpoint,
                    data={
                        "client_id": settings.microsoft_client_id,
                        "grant_type": "refresh_token",
                        "refresh_token": refresh_token,
                        "scope": credential.scopes,
                    },
                )
        except httpx.TimeoutException as error:
            raise MicrosoftProviderUnavailableError("Microsoft token refresh timed out") from error
        except httpx.HTTPError as error:
            raise MicrosoftProviderUnavailableError("Microsoft token refresh failed") from error
        if response.status_code in {400, 401, 403}:
            credential.revoked_at = datetime.now(UTC)
            await self.session.commit()
            raise MicrosoftAuthenticationError("Microsoft authorization must be renewed")
        if response.status_code != 200:
            raise MicrosoftProviderUnavailableError("Microsoft token refresh failed")
        try:
            body = response.json()
        except ValueError as error:
            raise MicrosoftGraphResponseError("Microsoft token refresh returned invalid data") from error
        access = body.get("access_token") if isinstance(body, dict) else None
        rotated = body.get("refresh_token", refresh_token) if isinstance(body, dict) else None
        if not isinstance(access, str) or not isinstance(rotated, str):
            raise MicrosoftAuthenticationError("Microsoft token refresh returned invalid data")
        expires = body.get("expires_in")
        credential.encrypted_access_token = encrypt_json(
            {"value": access}, purpose="microsoft-access-token"
        )
        credential.encrypted_refresh_token = encrypt_json(
            {"value": rotated}, purpose="microsoft-refresh-token"
        )
        credential.token_expires_at = (
            datetime.now(UTC) + timedelta(seconds=int(expires))
            if isinstance(expires, (int, float))
            else None
        )
        credential.updated_at = datetime.now(UTC)
        await self.session.commit()
        return access, rotated


class MicrosoftGraphClient:
    def __init__(
        self,
        resolver: MicrosoftCredentialResolver,
        context: NativeExecutionContext,
        *,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 30,
    ) -> None:
        self.resolver = resolver
        self.context = context
        self.client = client
        self.timeout_seconds = timeout_seconds

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
        body: dict[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        _, access, _ = await self.resolver.resolve(self.context)
        refreshed = False
        for attempt in range(3):
            try:
                response = await self._send(
                    method,
                    path,
                    access,
                    params=params,
                    body=body,
                    headers=headers,
                )
            except httpx.TimeoutException as error:
                raise MicrosoftProviderUnavailableError("Microsoft Graph request timed out") from error
            except httpx.HTTPError as error:
                raise MicrosoftProviderUnavailableError("Microsoft Graph request failed") from error
            if response.status_code == 401 and not refreshed:
                _, access, _ = await self.resolver.resolve(
                    self.context, force_refresh=True
                )
                refreshed = True
                continue
            if response.status_code == 403:
                raise MicrosoftPermissionError("Microsoft Graph permission denied")
            if response.status_code == 404:
                raise MicrosoftResourceNotFoundError("Microsoft Graph resource was not found")
            if response.status_code == 429 or response.status_code >= 500:
                if attempt < 2:
                    await asyncio.sleep(min(0.25 * (2**attempt), 1.0))
                    continue
                raise MicrosoftProviderUnavailableError("Microsoft Graph provider unavailable")
            if response.status_code < 200 or response.status_code >= 300:
                raise MicrosoftGraphResponseError("Microsoft Graph returned an unexpected response")
            if response.status_code == 204:
                return {}
            try:
                result = response.json()
            except ValueError as error:
                raise MicrosoftGraphResponseError("Microsoft Graph returned malformed JSON") from error
            if not isinstance(result, dict):
                raise MicrosoftGraphResponseError("Microsoft Graph returned an invalid result")
            return result
        raise MicrosoftProviderUnavailableError("Microsoft Graph provider unavailable")

    async def _send(self, method, path, access, *, params, body, headers):
        request_headers = {"Authorization": f"Bearer {access}"}
        if headers:
            request_headers.update(headers)
        if self.client is not None:
            return await self.client.request(
                method, f"{GRAPH_ROOT}{path}", params=params, json=body, headers=request_headers
            )
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            return await client.request(
                method, f"{GRAPH_ROOT}{path}", params=params, json=body, headers=request_headers
            )


def _address(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    email = value.get("emailAddress")
    if not isinstance(email, dict):
        return None
    address = email.get("address")
    return address if isinstance(address, str) else None


def _message(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise MicrosoftGraphResponseError("Microsoft Graph returned a malformed message")
    if not isinstance(value.get("id"), str) or not value["id"]:
        raise MicrosoftGraphResponseError("Microsoft Graph returned a message without an id")
    return {
        "id": value.get("id"),
        "conversation_id": value.get("conversationId"),
        "sender": _address(value.get("sender")),
        "to": [item for item in (_address(item) for item in value.get("toRecipients", [])) if item],
        "subject": str(value.get("subject") or ""),
        "received_at": value.get("receivedDateTime"),
        "is_unread": value.get("isRead") is False,
        "preview": str(value.get("bodyPreview") or "")[:1000],
        "has_attachments": bool(value.get("hasAttachments", False)),
    }


def _messages(body: dict[str, Any], limit: int) -> dict[str, Any]:
    values = body.get("value")
    if not isinstance(values, list):
        raise MicrosoftGraphResponseError("Microsoft Graph returned a malformed message list")
    return {
        "items": [_message(item) for item in values[:limit]],
        "count": min(len(values), limit),
        "next_page": body.get("@odata.nextLink") if isinstance(body.get("@odata.nextLink"), str) else None,
    }


def _draft(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": value.get("id"),
        "conversation_id": value.get("conversationId"),
        "subject": str(value.get("subject") or ""),
        "is_draft": True,
    }


async def _list(client: MicrosoftGraphClient, *, limit: int, unread: bool = False, query: str | None = None):
    params = {
        "$top": str(limit),
        "$orderby": "receivedDateTime desc",
        "$select": "id,conversationId,sender,toRecipients,subject,receivedDateTime,isRead,bodyPreview,hasAttachments",
    }
    if unread:
        params["$filter"] = "isRead eq false"
    if query:
        params["$search"] = f'"{query.replace(chr(34), "")}"'
    return _messages(await client.request("GET", "/me/messages", params=params), limit)


async def _client(session: AsyncSession, context: NativeExecutionContext) -> MicrosoftGraphClient:
    return MicrosoftGraphClient(MicrosoftCredentialResolver(session), context)


def build_native_registry(session: AsyncSession) -> NativeToolRegistry:
    async def list_recent(args: _LimitArgs, context: NativeExecutionContext):
        return await _list(await _client(session, context), limit=args.limit)

    async def list_unread(args: _LimitArgs, context: NativeExecutionContext):
        return await _list(await _client(session, context), limit=args.limit, unread=True)

    async def search(args: _SearchArgs, context: NativeExecutionContext):
        return await _list(await _client(session, context), limit=args.limit, query=args.query)

    async def get_thread(args: _MessageArgs, context: NativeExecutionContext):
        body = await (await _client(session, context)).request(
            "GET", f"/me/messages/{args.message_id}",
            params={"$select": "id,conversationId,sender,toRecipients,subject,receivedDateTime,isRead,bodyPreview,hasAttachments"},
        )
        message = _message(body)
        if not message["conversation_id"]:
            return {"items": [message], "count": 1}
        thread = await (await _client(session, context)).request(
            "GET", "/me/messages",
            params={
                "$filter": f"conversationId eq '{message['conversation_id']}'",
                "$orderby": "receivedDateTime asc",
                "$top": str(MAX_PAGE_SIZE),
                "$select": "id,conversationId,sender,toRecipients,subject,receivedDateTime,isRead,bodyPreview,hasAttachments",
            },
        )
        return _messages(thread, MAX_PAGE_SIZE)

    async def action_required(args: _LimitArgs, context: NativeExecutionContext):
        result = await _list(await _client(session, context), limit=args.limit)
        result["items"] = [
            item for item in result["items"]
            if item["is_unread"] or "?" in item["preview"] or "please" in item["preview"].lower()
        ]
        result["count"] = len(result["items"])
        return result

    async def unanswered(args: _LimitArgs, context: NativeExecutionContext):
        result = await _list(await _client(session, context), limit=args.limit, unread=False)
        result["items"] = [item for item in result["items"] if item["is_unread"]]
        result["count"] = len(result["items"])
        return result

    async def reply_draft(args: _DraftReplyArgs, context: NativeExecutionContext):
        body = await (await _client(session, context)).request(
            "POST", f"/me/messages/{args.message_id}/createReply",
            body={"message": {"body": {"contentType": "Text", "content": args.body}}},
        )
        return _draft(body)

    async def new_draft(args: _NewDraftArgs, context: NativeExecutionContext):
        body = {
            "subject": args.subject,
            "body": {"contentType": "Text", "content": args.body},
            "toRecipients": [{"emailAddress": {"address": value}} for value in args.to],
            "ccRecipients": [{"emailAddress": {"address": value}} for value in args.cc],
            "bccRecipients": [{"emailAddress": {"address": value}} for value in args.bcc],
        }
        return _draft(await (await _client(session, context)).request("POST", "/me/messages", body=body))

    async def proposed_response(args: _ProposedResponseArgs, context: NativeExecutionContext):
        return {"message_id": args.message_id, "summary": args.response[:1000]}

    async def send_reply(args: _DraftReplyArgs, context: NativeExecutionContext):
        await (await _client(session, context)).request(
            "POST", f"/me/messages/{args.message_id}/reply",
            body={"message": {"body": {"contentType": "Text", "content": args.body}}},
        )
        return {"sent": True, "message_id": args.message_id}

    async def send_new(args: _NewDraftArgs, context: NativeExecutionContext):
        await (await _client(session, context)).request(
            "POST", "/me/sendMail",
            body={"message": {"subject": args.subject, "body": {"contentType": "Text", "content": args.body}, "toRecipients": [{"emailAddress": {"address": value}} for value in args.to]}, "saveToSentItems": True},
        )
        return {"sent": True, "recipient_count": len(args.to)}

    async def archive(args: _MessageArgs, context: NativeExecutionContext):
        client = await _client(session, context)
        folders = await client.request(
            "GET", "/me/mailFolders/archive", params={"$select": "id"}
        )
        archive_id = folders.get("id")
        if not isinstance(archive_id, str) or not archive_id:
            raise MicrosoftGraphResponseError("Microsoft archive folder is unavailable")
        await client.request(
            "POST", f"/me/messages/{args.message_id}/move", body={"destinationId": archive_id}
        )
        return {"archived": True, "message_id": args.message_id}

    async def delete(args: _MessageArgs, context: NativeExecutionContext):
        await (await _client(session, context)).request("DELETE", f"/me/messages/{args.message_id}")
        return {"deleted": True, "message_id": args.message_id}

    specs = (
        ("email.list_recent", "email_list_recent", "List recent Outlook messages", _LimitArgs, NativeToolAction.READ, list_recent),
        ("email.list_unread", "email_list_unread", "List unread Outlook messages", _LimitArgs, NativeToolAction.READ, list_unread),
        ("email.search", "email_search", "Search Outlook messages", _SearchArgs, NativeToolAction.READ, search),
        ("email.get_thread", "email_get_thread", "Open an Outlook message thread", _MessageArgs, NativeToolAction.READ, get_thread),
        ("email.action_required", "email_action_required", "Find Outlook messages requiring action", _LimitArgs, NativeToolAction.READ, action_required),
        ("email.unanswered", "email_unanswered", "Find unanswered Outlook messages", _LimitArgs, NativeToolAction.READ, unanswered),
        ("email.create_reply_draft", "email_create_reply_draft", "Create an Outlook reply draft", _DraftReplyArgs, NativeToolAction.DRAFT, reply_draft),
        ("email.create_new_draft", "email_create_new_draft", "Create an Outlook email draft", _NewDraftArgs, NativeToolAction.DRAFT, new_draft),
        ("email.proposed_response", "email_proposed_response", "Summarize a proposed Outlook response", _ProposedResponseArgs, NativeToolAction.DRAFT, proposed_response),
        ("email.send_reply", "email_send_reply", "Send an Outlook reply", _DraftReplyArgs, NativeToolAction.WRITE, send_reply),
        ("email.send_new", "email_send_new", "Send a new Outlook email", _NewDraftArgs, NativeToolAction.WRITE, send_new),
        ("email.archive", "email_archive", "Archive an Outlook message", _MessageArgs, NativeToolAction.DESTRUCTIVE, archive),
        ("email.delete", "email_delete", "Delete an Outlook message", _MessageArgs, NativeToolAction.DESTRUCTIVE, delete),
    )
    return NativeToolRegistry(tuple(
        NativeToolSpec(
            canonical_name=canonical,
            wire_name=wire,
            description=description,
            arguments_model=model,
            action=action,
            provider=PROVIDER,
            provider_account_required=True,
            handler=handler,
            provenance_metadata={"source": "microsoft_graph", "version": "1"},
        )
        for canonical, wire, description, model, action, handler in specs
    ))
