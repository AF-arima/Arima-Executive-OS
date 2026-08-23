from dataclasses import dataclass
from decimal import Decimal
from html import escape
from urllib.parse import urlencode
from uuid import UUID


@dataclass(frozen=True, slots=True)
class EmailTemplate:
    subject: str
    text_body: str
    html_body: str


def withdrawal_intake_email(
    *,
    full_name: str,
    amount_eth: Decimal,
    wallet_address: str,
    network: str,
    note: str | None,
    account_email: str,
    workspace_reference: str,
    submitted_at: str,
) -> EmailTemplate:
    safe = {
        "full_name": escape(full_name),
        "amount_eth": escape(str(amount_eth)),
        "wallet_address": escape(wallet_address),
        "network": escape(network),
        "note": escape(note or "No note provided"),
        "account_email": escape(account_email),
        "workspace_reference": escape(workspace_reference),
        "submitted_at": escape(submitted_at),
    }
    text_body = (
        "New withdrawal request intake submission (no funds moved).\n\n"
        f"Full name: {full_name}\n"
        f"Amount (ETH): {amount_eth}\n"
        f"Wallet address: {wallet_address}\n"
        f"Network: {network}\n"
        f"Note: {note or 'No note provided'}\n"
        f"Account email: {account_email}\n"
        f"Workspace: {workspace_reference}\n"
        f"Submitted at: {submitted_at}\n\n"
        "The customer was told that the team will respond within 48 hours."
    )
    html_body = (
        "<h2>New withdrawal request intake submission</h2>"
        "<p><strong>No funds were moved, reserved, or recorded.</strong></p>"
        "<ul>"
        f"<li>Full name: {safe['full_name']}</li>"
        f"<li>Amount (ETH): {safe['amount_eth']}</li>"
        f"<li>Wallet address: {safe['wallet_address']}</li>"
        f"<li>Network: {safe['network']}</li>"
        f"<li>Note: {safe['note']}</li>"
        f"<li>Account email: {safe['account_email']}</li>"
        f"<li>Workspace: {safe['workspace_reference']}</li>"
        f"<li>Submitted at: {safe['submitted_at']}</li>"
        "</ul>"
        "<p>The customer was told that the team will respond within 48 hours.</p>"
    )
    return EmailTemplate(
        subject=f"Withdrawal request intake: {full_name}",
        text_body=text_body,
        html_body=html_body,
    )


def verification_email(
    *,
    recipient_name: str,
    verification_url: str,
) -> EmailTemplate:
    safe_name = escape(recipient_name)
    safe_url = escape(verification_url, quote=True)
    return EmailTemplate(
        subject="Verify your Arima Executive OS email",
        text_body=(
            f"Hello {recipient_name},\n\n"
            "Verify your email address to activate your Arima Executive OS "
            f"account:\n{verification_url}\n\n"
            "If you did not create this account, you can ignore this email."
        ),
        html_body=(
            f"<p>Hello {safe_name},</p><p>Verify your email address to "
            "activate your Arima Executive OS account.</p>"
            f"<p><a href=\"{safe_url}\">Verify email address</a></p>"
            "<p>If you did not create this account, you can ignore this email.</p>"
        ),
    )


def password_reset_email(
    *,
    recipient_name: str,
    reset_url: str,
) -> EmailTemplate:
    safe_name = escape(recipient_name)
    safe_url = escape(reset_url, quote=True)
    return EmailTemplate(
        subject="Reset your Arima Executive OS password",
        text_body=(
            f"Hello {recipient_name},\n\n"
            "Use this secure link to reset your password:\n"
            f"{reset_url}\n\n"
            "If you did not request this change, secure your account and "
            "contact support."
        ),
        html_body=(
            f"<p>Hello {safe_name},</p><p>Use this secure link to reset "
            "your password.</p>"
            f"<p><a href=\"{safe_url}\">Reset password</a></p>"
            "<p>If you did not request this change, secure your account and "
            "contact support.</p>"
        ),
    )


def email_change_email(
    *,
    recipient_name: str,
    change_url: str,
) -> EmailTemplate:
    safe_name = escape(recipient_name)
    safe_url = escape(change_url, quote=True)
    return EmailTemplate(
        subject="Confirm your new Arima Executive OS email",
        text_body=(
            f"Hello {recipient_name},\n\n"
            "Confirm this email address for your Arima Executive OS account:\n"
            f"{change_url}\n\n"
            "If you did not request this change, secure your account and "
            "contact support."
        ),
        html_body=(
            f"<p>Hello {safe_name},</p><p>Confirm this email address for "
            "your Arima Executive OS account.</p>"
            f"<p><a href=\"{safe_url}\">Confirm email change</a></p>"
            "<p>If you did not request this change, secure your account and "
            "contact support.</p>"
        ),
    )


def welcome_email(*, recipient_name: str) -> EmailTemplate:
    safe_name = escape(recipient_name)
    return EmailTemplate(
        subject="Welcome to Arima Executive OS",
        text_body=(
            f"Welcome {recipient_name},\n\n"
            "Your Arima Executive OS workspace is ready."
        ),
        html_body=(
            f"<p>Welcome {safe_name},</p>"
            "<p>Your Arima Executive OS workspace is ready.</p>"
        ),
    )


def login_notification_email(
    *,
    recipient_name: str,
    ip_address: str | None,
    user_agent: str | None,
) -> EmailTemplate:
    location = ip_address or "an unknown network"
    device = user_agent or "an unknown device"
    safe_name = escape(recipient_name)
    safe_location = escape(location)
    safe_device = escape(device)
    return EmailTemplate(
        subject="New sign-in to Arima Executive OS",
        text_body=(
            f"Hello {recipient_name},\n\nA sign-in to your Arima Executive "
            f"OS account was detected from {location} using {device}.\n\n"
            "If this was not you, reset your password immediately."
        ),
        html_body=(
            f"<p>Hello {safe_name},</p><p>A sign-in to your Arima "
            f"Executive OS account was detected from {safe_location} using {safe_device}."
            "</p><p>If this was not you, reset your password immediately.</p>"
        ),
    )


def security_alert_email(
    *,
    recipient_name: str,
    event: str,
) -> EmailTemplate:
    safe_name = escape(recipient_name)
    safe_event = escape(event)
    return EmailTemplate(
        subject="Security alert for Arima Executive OS",
        text_body=(
            f"Hello {recipient_name},\n\n"
            f"Security event: {event}.\n\n"
            "If you did not initiate this activity, reset your password and "
            "contact support."
        ),
        html_body=(
            f"<p>Hello {safe_name},</p><p>Security event: {safe_event}.</p>"
            "<p>If you did not initiate this activity, reset your password and "
            "contact support.</p>"
        ),
    )


def withdrawal_received_email(
    *, recipient_name: str, request_id: UUID, amount: Decimal,
    currency: str, masked_wallet: str, network: str, support_email: str,
) -> EmailTemplate:
    subject = "Withdrawal request received"
    text = (
        f"Hello {recipient_name},\n\n"
        "Your withdrawal request has been received and is under review.\n\n"
        f"Request ID: {request_id}\nAmount: {amount} {currency}\n"
        f"Destination: {masked_wallet}\nNetwork: {network}\n\n"
        "Our team will review it and communicate within 24 hours. "
        f"Support: {support_email}\n\nThis does not confirm completion."
    )
    return EmailTemplate(subject=subject, text_body=text, html_body=escape(text).replace("\n", "<br>"))


def link(
    base_url: str,
    path: str,
    token: str,
    *,
    parameters: dict[str, str] | None = None,
) -> str:
    query = {"token": token, **(parameters or {})}
    return f"{base_url.rstrip('/')}{path}?{urlencode(query)}"
