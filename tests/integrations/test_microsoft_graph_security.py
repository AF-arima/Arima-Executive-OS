from uuid import uuid4

import pytest

from app.integrations.microsoft_graph import (
    MicrosoftAmbiguousAccountError,
    MicrosoftCredentialResolver,
    MicrosoftNotConfiguredError,
)
from app.orchestration.native_tools import NativeExecutionContext


class ScalarRows:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class Session:
    def __init__(self, rows):
        self.rows = rows

    async def scalars(self, query):
        return ScalarRows(self.rows)


def context():
    return NativeExecutionContext(
        tenant_id=uuid4(),
        workspace_id=uuid4(),
        actor_id=uuid4(),
        provider="microsoft",
        provider_account_id="account-1",
        agent="executive",
    )


@pytest.mark.asyncio
async def test_missing_or_ambiguous_account_fails_closed(monkeypatch):
    monkeypatch.setattr(
        "app.integrations.microsoft_graph.get_settings",
        lambda: type("Settings", (), {"microsoft_integration_enabled": True})(),
    )
    with pytest.raises(MicrosoftNotConfiguredError):
        await MicrosoftCredentialResolver(Session([])).resolve(context())
    with pytest.raises(MicrosoftAmbiguousAccountError):
        await MicrosoftCredentialResolver(Session([object(), object()])).resolve(context())
