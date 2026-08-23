import asyncio
from types import SimpleNamespace
from uuid import UUID

import pytest
from sqlalchemy import select

from app.database.models import AuditLog
from tests.auth.conftest import AuthTestContext
from tests.auth.helpers import bearer, login_user, register_user


pytest_plugins = ("tests.auth.conftest",)


def valid_payload() -> dict[str, str]:
    return {
        "full_name": "Ada Lovelace",
        "amount_eth": "1.25",
        "wallet_address": "0x1234567890abcdef1234567890abcdef12345678",
        "network": "Ethereum Mainnet",
        "note": "Please contact me by email.",
    }


@pytest.fixture
def configured_intake_email(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.api.v1.routes.withdrawal_intake.get_settings",
        lambda: SimpleNamespace(
            email_from_address="operator@example.com",
            withdrawal_intake_rate_limit_per_minute=3,
        ),
    )


def test_withdrawal_intake_requires_authentication(
    auth_context: AuthTestContext,
) -> None:
    response = auth_context.client.post(
        "/api/v1/requests/withdrawal-intake",
        json=valid_payload(),
    )
    assert response.status_code == 401


def test_withdrawal_intake_validates_wallet_and_amount(
    auth_context: AuthTestContext,
) -> None:
    register_user(auth_context, "intake-validation@example.com")
    headers = bearer(login_user(auth_context, "intake-validation@example.com")["access_token"])

    invalid = valid_payload()
    invalid["wallet_address"] = "0xnot-a-wallet"
    invalid["amount_eth"] = "0"
    response = auth_context.client.post(
        "/api/v1/requests/withdrawal-intake",
        headers=headers,
        json=invalid,
    )
    assert response.status_code == 422


def test_withdrawal_intake_emails_operator_and_audits_without_financial_record(
    auth_context: AuthTestContext,
    configured_intake_email: None,
) -> None:
    user = register_user(auth_context, "intake-user@example.com")
    headers = bearer(login_user(auth_context, "intake-user@example.com")["access_token"])

    response = auth_context.client.post(
        "/api/v1/requests/withdrawal-intake",
        headers=headers,
        json=valid_payload(),
    )

    assert response.status_code == 202
    assert response.json()["message"] == (
        "Your request has been received. Our team will contact you within 48 hours."
    )
    message = auth_context.email_provider.messages[-1]
    assert message.to_address == "operator@example.com"
    assert "Ada Lovelace" in message.text_body
    assert "1.25" in message.text_body
    assert valid_payload()["wallet_address"] in message.text_body
    assert "no funds moved" in message.text_body

    user_id = UUID(str(user["id"]))

    async def read_audit() -> AuditLog | None:
        async with auth_context.session_factory() as session:
            return await session.scalar(
                select(AuditLog)
                .where(
                    AuditLog.actor_id == user_id,
                    AuditLog.event_type == "withdrawal_request_intake_submitted",
                )
            )

    audit = asyncio.run(read_audit())
    assert audit is not None
    assert audit.event_metadata["amount_eth"] == "1.25"
