from __future__ import annotations

import base64
import hashlib
import json
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.database.models import OAuthCredential, OAuthState, WorkspaceMembership
from app.integrations.secret_box import decrypt_json, encrypt_json

PROVIDER = "microsoft"
SCOPES = ("offline_access", "User.Read", "Mail.Read", "Mail.ReadWrite")
GRAPH_ME = "https://graph.microsoft.com/v1.0/me"
GRAPH_INBOX = "https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages"


class MicrosoftIntegrationError(RuntimeError):
    pass


def _settings():
    settings = get_settings()
    if not settings.microsoft_redirect_uri:
        raise MicrosoftIntegrationError("Microsoft redirect URI is not configured")
    return settings


def _challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


def _state_hash(state: str) -> str:
    return hashlib.sha256(state.encode()).hexdigest()


def _utc_datetime(value: datetime) -> datetime:
    """Normalize database datetimes to timezone-aware UTC for comparisons."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


async def require_workspace(session: AsyncSession, actor_id: UUID, workspace_id: UUID) -> None:
    membership = await session.scalar(select(WorkspaceMembership).where(
        WorkspaceMembership.user_id == actor_id,
        WorkspaceMembership.workspace_id == workspace_id,
    ))
    if membership is None:
        raise MicrosoftIntegrationError("workspace authorization denied")


async def authorize_url(session: AsyncSession, actor_id: UUID, workspace_id: UUID) -> str:
    settings = _settings()
    await require_workspace(session, actor_id, workspace_id)
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    now = datetime.now(UTC)
    session.add(OAuthState(
        state_hash=_state_hash(state), actor_id=actor_id,
        tenant_id=workspace_id, workspace_id=workspace_id,
        encrypted_code_verifier=encrypt_json({"value": verifier}, purpose="microsoft-pkce"),
        redirect_uri=settings.microsoft_redirect_uri,
        expires_at=now + timedelta(minutes=10),
    ))
    await session.commit()
    endpoint = f"{settings.microsoft_authority.rstrip('/')}/oauth2/v2.0/authorize"
    return endpoint + "?" + urlencode({
        "client_id": settings.microsoft_client_id,
        "response_type": "code",
        "redirect_uri": settings.microsoft_redirect_uri,
        "response_mode": "query",
        "scope": " ".join(SCOPES),
        "state": state,
        "code_challenge": _challenge(verifier),
        "code_challenge_method": "S256",
    })


async def _exchange(code: str, verifier: str, redirect_uri: str) -> dict[str, object]:
    settings = _settings()
    endpoint = f"{settings.microsoft_authority.rstrip('/')}/oauth2/v2.0/token"
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(endpoint, data={
            "client_id": settings.microsoft_client_id,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": verifier,
            "scope": " ".join(SCOPES),
        })
    if response.status_code != 200:
        raise MicrosoftIntegrationError(f"Microsoft token exchange failed ({response.status_code})")
    body = response.json()
    if not isinstance(body, dict) or not body.get("access_token") or not body.get("refresh_token"):
        raise MicrosoftIntegrationError("Microsoft token exchange returned an invalid result")
    return body


async def _graph(access_token: str, url: str, *, params: dict[str, str] | None = None) -> dict[str, object]:
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(url, params=params, headers={"Authorization": f"Bearer {access_token}"})
    if response.status_code != 200:
        raise MicrosoftIntegrationError(f"Microsoft Graph read failed ({response.status_code})")
    body = response.json()
    if not isinstance(body, dict):
        raise MicrosoftIntegrationError("Microsoft Graph returned an invalid result")
    return body


async def complete_callback(session: AsyncSession, *, state: str, code: str) -> OAuthCredential:
    now = datetime.now(UTC)
    record = await session.scalar(select(OAuthState).where(OAuthState.state_hash == _state_hash(state)))
    if (
        record is None
        or record.consumed_at is not None
        or _utc_datetime(record.expires_at) <= _utc_datetime(now)
    ):
        raise MicrosoftIntegrationError("Microsoft OAuth state is invalid or expired")
    verifier_payload = decrypt_json(record.encrypted_code_verifier, purpose="microsoft-pkce")
    verifier = verifier_payload.get("value")
    if not isinstance(verifier, str) or not verifier:
        raise MicrosoftIntegrationError("Microsoft OAuth verifier is invalid")
    tokens = await _exchange(code, verifier, record.redirect_uri)
    profile = await _graph(str(tokens["access_token"]), GRAPH_ME)
    account_id = profile.get("id")
    if not isinstance(account_id, str) or not account_id:
        raise MicrosoftIntegrationError("Microsoft account identity is unavailable")
    credential = await session.scalar(select(OAuthCredential).where(
        OAuthCredential.actor_id == record.actor_id,
        OAuthCredential.workspace_id == record.workspace_id,
        OAuthCredential.tenant_id == record.tenant_id,
        OAuthCredential.provider == PROVIDER,
        OAuthCredential.provider_account_id == account_id,
    ))
    expires = tokens.get("expires_in")
    expires_at = now + timedelta(seconds=int(expires)) if isinstance(expires, (int, float)) else None
    values = dict(
        tenant_id=record.tenant_id, workspace_id=record.workspace_id, actor_id=record.actor_id,
        provider=PROVIDER, provider_account_id=account_id,
        encrypted_access_token=encrypt_json({"value": str(tokens["access_token"])}, purpose="microsoft-access-token"),
        encrypted_refresh_token=encrypt_json({"value": str(tokens["refresh_token"])}, purpose="microsoft-refresh-token"),
        token_expires_at=expires_at, scopes=" ".join(SCOPES), revoked_at=None,
    )
    if credential is None:
        credential = OAuthCredential(**values)
        session.add(credential)
    else:
        for key, value in values.items():
            setattr(credential, key, value)
    record.consumed_at = now
    await session.commit()
    return credential


async def status(session: AsyncSession, actor_id: UUID, workspace_id: UUID) -> dict[str, object]:
    await require_workspace(session, actor_id, workspace_id)
    credential = await session.scalar(select(OAuthCredential).where(
        OAuthCredential.actor_id == actor_id, OAuthCredential.workspace_id == workspace_id,
        OAuthCredential.tenant_id == workspace_id, OAuthCredential.provider == PROVIDER,
        OAuthCredential.revoked_at.is_(None),
    ))
    if credential is None:
        return {"provider": PROVIDER, "status": "NOT_CONFIGURED"}
    try:
        from app.integrations.microsoft_graph import (
            MicrosoftGraphClient,
            MicrosoftCredentialResolver,
        )
        from app.orchestration.native_tools import NativeExecutionContext

        context = NativeExecutionContext(
            tenant_id=credential.tenant_id,
            workspace_id=credential.workspace_id,
            actor_id=credential.actor_id,
            provider=PROVIDER,
            provider_account_id=credential.provider_account_id,
            agent="integration_verification",
        )
        await MicrosoftGraphClient(
            MicrosoftCredentialResolver(session), context
        ).request(
            "GET",
            "/me/mailFolders/inbox/messages",
            params={"$top": "1", "$select": "id"},
        )
    except Exception:
        return {"provider": PROVIDER, "status": "CONFIGURED_BUT_UNVERIFIED", "account_id": credential.provider_account_id}
    return {"provider": PROVIDER, "status": "LIVE", "account_id": credential.provider_account_id}


async def disconnect(session: AsyncSession, actor_id: UUID, workspace_id: UUID) -> None:
    await require_workspace(session, actor_id, workspace_id)
    records = (await session.scalars(select(OAuthCredential).where(
        OAuthCredential.actor_id == actor_id, OAuthCredential.workspace_id == workspace_id,
        OAuthCredential.tenant_id == workspace_id, OAuthCredential.provider == PROVIDER,
        OAuthCredential.revoked_at.is_(None),
    ))).all()
    now = datetime.now(UTC)
    for record in records:
        record.revoked_at = now
    await session.commit()
