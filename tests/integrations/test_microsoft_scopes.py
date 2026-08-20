from __future__ import annotations

from uuid import uuid4

import pytest

from app.integrations import microsoft


class _Session:
    def add(self, value) -> None:
        self.value = value

    async def commit(self) -> None:
        return None


@pytest.mark.asyncio
async def test_authorize_requests_only_read_scopes(monkeypatch) -> None:
    settings = type(
        "Settings",
        (),
        {
            "microsoft_redirect_uri": "http://localhost:8000/callback",
            "microsoft_authority": "https://login.microsoftonline.com/common",
            "microsoft_client_id": "client-id",
        },
    )()
    monkeypatch.setattr(microsoft, "get_settings", lambda: settings)
    monkeypatch.setattr(
        microsoft,
        "encrypt_json",
        lambda value, *, purpose: "encrypted-verifier",
    )
    async def allow_workspace(*_) -> None:
        return None

    monkeypatch.setattr(microsoft, "require_workspace", allow_workspace)

    session = _Session()
    url = await microsoft.authorize_url(session, uuid4(), uuid4())

    assert "Mail.Read" in url
    assert "Mail.ReadWrite" not in url
    assert "offline_access" in url
    assert "User.Read" in url
