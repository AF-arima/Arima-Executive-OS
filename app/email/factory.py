from functools import lru_cache

from app.auth.exceptions import EmailDeliveryError
from app.core.config import get_settings
from app.email.providers import ResendEmailProvider, SmtpEmailProvider
from app.email.registry import registry
from app.email.service import TransactionalEmailService


def _register_default_providers() -> None:
    registry.register("resend", lambda: ResendEmailProvider(get_settings()))
    registry.register("smtp", lambda: SmtpEmailProvider(get_settings()))


_register_default_providers()


@lru_cache
def get_transactional_email_service() -> TransactionalEmailService:
    settings = get_settings()
    if not settings.email_provider:
        raise EmailDeliveryError(
            "EMAIL_PROVIDER must be configured for transactional email"
        )
    try:
        provider = registry.create(settings.email_provider)
    except KeyError as error:
        raise EmailDeliveryError(str(error)) from error
    return TransactionalEmailService(provider, settings)
