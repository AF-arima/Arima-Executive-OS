from __future__ import annotations

from collections.abc import Iterator

import pytest

from app.core.config import get_settings
from app.providers import ProviderName
from app.providers.exceptions import ProviderUnavailable
from app.voice.observability import VoiceExecutionObserver
from tests.auth.conftest import AuthTestContext
from tests.auth.helpers import bearer, csrf_headers, grant_role, login_user, register_user


@pytest.fixture
def founder_allowlist(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("FOUNDER_CONTROL_EMAILS", "founder@example.com")
    get_settings.cache_clear()
    try:
        yield
    finally:
        get_settings.cache_clear()


def _founder_headers(context: AuthTestContext) -> dict[str, str]:
    return {
        **bearer(login_user(context, "founder@example.com")["access_token"]),
        **csrf_headers(context),
    }


def _endpoint() -> str:
    return "/api/v1/admin/founder/diagnostics/groq-smoke"


def test_groq_smoke_requires_founder_control(
    management_context: AuthTestContext,
    founder_allowlist: None,
) -> None:
    register_user(management_context, "normal@example.com")
    register_user(management_context, "founder@example.com")
    grant_role(management_context, "founder@example.com", "administrator")

    unauthenticated = management_context.client.post(_endpoint())
    non_founder = management_context.client.post(
        _endpoint(),
        headers={
            **bearer(login_user(management_context, "normal@example.com")["access_token"]),
            **csrf_headers(management_context),
        },
    )

    assert unauthenticated.status_code == 401
    assert non_founder.status_code == 403


def test_groq_smoke_uses_fixed_request_once_and_returns_sanitized_success(
    management_context: AuthTestContext,
    founder_allowlist: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    register_user(management_context, "founder@example.com")
    grant_role(management_context, "founder@example.com", "administrator")
    observed: dict[str, object] = {"calls": 0}

    class FakeProvider:
        async def complete(self, request: object) -> object:
            observed["calls"] = int(observed["calls"]) + 1
            completion_request = request
            observed["model"] = completion_request.model
            observed["prompt"] = completion_request.messages[0].content
            observer = completion_request.metadata["_voice_observer"]
            assert isinstance(observer, VoiceExecutionObserver)
            observer.emit("provider_attempt_start", provider="groq")
            observer.emit(
                "provider_response_received",
                provider="groq",
                status_category="2xx",
            )
            observer.emit("provider_attempt_success", provider="groq")
            return type("Result", (), {"content": "GROQ_SMOKE_OK"})()

    class FakeFactory:
        def create(self, *, provider: ProviderName, model: str) -> FakeProvider:
            observed["provider"] = provider
            observed["factory_model"] = model
            return FakeProvider()

    monkeypatch.setattr("app.api.v1.routes.admin.ProviderFactory", FakeFactory)
    response = management_context.client.post(
        _endpoint(),
        json={"prompt": "caller-controlled prompt must be ignored"},
        headers=_founder_headers(management_context),
    )

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "provider": "groq",
        "model": "openai/gpt-oss-20b",
        "http_status_category": "2xx",
        "elapsed_ms": response.json()["elapsed_ms"],
        "parser": "pass",
        "completion_matches": True,
        "telemetry": "pass",
        "error": None,
    }
    assert observed == {
        "calls": 1,
        "provider": ProviderName.GROQ,
        "factory_model": "openai/gpt-oss-20b",
        "model": "openai/gpt-oss-20b",
        "prompt": "Reply with exactly: GROQ_SMOKE_OK",
    }
    assert "GROQ_SMOKE_OK" not in response.text
    assert "caller-controlled" not in response.text
    assert "secret" not in response.text.lower()


def test_groq_smoke_sanitizes_provider_failure_without_retry(
    management_context: AuthTestContext,
    founder_allowlist: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    register_user(management_context, "founder@example.com")
    grant_role(management_context, "founder@example.com", "administrator")
    calls = 0

    class FakeProvider:
        async def complete(self, request: object) -> object:
            nonlocal calls
            calls += 1
            observer = request.metadata["_voice_observer"]
            observer.emit(
                "provider_attempt_failure",
                provider="groq",
                failure_class="provider_unavailable",
                exception_type="ProviderUnavailable",
            )
            raise ProviderUnavailable("secret provider response")

    class FakeFactory:
        def create(self, *, provider: ProviderName, model: str) -> FakeProvider:
            assert provider is ProviderName.GROQ
            assert model == "openai/gpt-oss-20b"
            return FakeProvider()

    monkeypatch.setattr("app.api.v1.routes.admin.ProviderFactory", FakeFactory)
    response = management_context.client.post(
        _endpoint(),
        headers=_founder_headers(management_context),
    )

    assert response.status_code == 200
    assert response.json()["success"] is False
    assert response.json()["error"] == "provider_unavailable"
    assert response.json()["telemetry"] == "pass"
    assert calls == 1
    assert "secret provider response" not in response.text
    assert "ProviderUnavailable" not in response.text
